"""Workday job-search collector.

Workday-hosted career sites (used by Intel, Lonza, Amgen, Palo Alto Networks,
and many other large employers Roman has worked for or is targeting) expose
an internal JSON search endpoint that powers their own career-site search
box:

    POST https://{tenant}.{wd_host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
    Content-Type: application/json
    {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}

This is NOT an officially documented public API the way Greenhouse's is —
Workday doesn't publish it — but it requires no auth, is exactly what the
career page's own UI calls, and has been stable for years. Each company's
`tenant`, `wd_host` (wd1-wd5), and `site` name are company-specific and must
be identified individually (inspect the career page's network requests, or
brute-force common site-name patterns against a few wd hosts). Store them in
config/companies.yaml like:

    - name: Lonza
      ats_type: workday
      board_identifier: lonza          # the tenant
      custom_config:
        wd_host: wd3
        site: Lonza_Careers

Job descriptions are intentionally NOT fetched in fetch_jobs() — the search
endpoint doesn't return them, and some employers here post 1000+ openings,
so eagerly fetching a detail page per job on every poll would be wasteful
and slow. Call fetch_job_description() lazily, only for postings that
already survived eligibility filtering (see src/processing).
"""
from __future__ import annotations

from src.collectors.base import BaseCollector, CollectorError, CompanySource, RawJob

PAGE_SIZE = 20  # Workday's cxs search endpoint rejects limit > 20 with a 400
MAX_PAGES = 200  # hard cap (~4000 jobs) so a runaway board can't loop forever


class WorkdayCollector(BaseCollector):
    source_name = "workday"

    def _resolve_target(self, company: CompanySource) -> tuple[str, str, str]:
        tenant = company.board_identifier
        config = company.custom_config or {}
        wd_host = config.get("wd_host")
        site = config.get("site")
        if not tenant or not wd_host or not site:
            raise CollectorError(
                self.source_name,
                company.name,
                "requires board_identifier (tenant) plus custom_config.wd_host and custom_config.site",
            )
        return tenant, wd_host, site

    def fetch_jobs(self, company: CompanySource) -> list[RawJob]:
        tenant, wd_host, site = self._resolve_target(company)
        search_url = f"https://{tenant}.{wd_host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"

        jobs: list[RawJob] = []
        offset = 0
        total: int | None = None
        for _ in range(MAX_PAGES):
            body = {"appliedFacets": {}, "limit": PAGE_SIZE, "offset": offset, "searchText": ""}
            data = self.post_json(search_url, body, _company_name=company.name)

            if not isinstance(data, dict) or "jobPostings" not in data:
                raise CollectorError(self.source_name, company.name, "unexpected response shape (no 'jobPostings' key)")

            postings = data["jobPostings"]
            # Some Workday tenants only report an accurate "total" on the
            # first page and send 0 on later pages despite still returning
            # postings — trust the first non-zero total we see, not the last.
            page_total = data.get("total")
            if page_total:
                total = page_total

            if not postings:
                break

            for item in postings:
                if not isinstance(item, dict) or "externalPath" not in item or "title" not in item:
                    continue
                external_path = item["externalPath"]
                job_url = f"https://{tenant}.{wd_host}.myworkdayjobs.com/{site}{external_path}"
                jobs.append(
                    RawJob(
                        external_id=external_path,
                        title=item.get("title") or "",
                        location_text=item.get("locationsText"),
                        description_html=None,  # see module docstring; fetch lazily via fetch_job_description()
                        apply_url=job_url,
                        source_url=job_url,
                        posted_at=item.get("postedOn"),
                        department=None,
                        raw=item,
                    )
                )

            offset += PAGE_SIZE
            if total is not None and offset >= total:
                break

        return jobs

    def fetch_job_description(self, company: CompanySource, external_path: str) -> str | None:
        """Fetch one job's full description on demand (GET, unlike the
        POST-based search endpoint). Only call this for postings that have
        already passed eligibility filtering — not for every job found."""
        tenant, wd_host, site = self._resolve_target(company)
        detail_url = f"https://{tenant}.{wd_host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{external_path}"
        data = self.get_json(detail_url, _company_name=company.name)
        if not isinstance(data, dict):
            raise CollectorError(self.source_name, company.name, "unexpected job detail response shape")
        return (data.get("jobPostingInfo") or {}).get("jobDescription")

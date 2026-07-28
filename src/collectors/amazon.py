"""Amazon jobs collector.

Amazon's public careers site calls an unauthenticated JSON search endpoint
that isn't officially documented but requires no auth and has been stable
for years — the same category of "not an official public API, but exactly
what the site's own UI calls" as the Workday collector:

    GET https://www.amazon.jobs/en/search.json?offset={offset}&result_limit={n}&sort=recent

No board_identifier/custom_config is needed — Amazon has one board for the
whole company (job_category/job_family in the response already carries the
AWS-vs-retail-vs-devices distinction, which normalize.py doesn't need to
know about since eligibility/scoring works off title + description text).

Unlike Workday, Amazon's search response already includes the full job
description and qualifications inline, so there's no lazy detail-fetch step
here. A page-count cap (see MAX_PAGES) keeps one poll from trying to pull
Amazon's entire global posting volume (tens of thousands of jobs across
every country) every 3 hours — sort=recent means the cap trims off the
*oldest* postings first, not an arbitrary slice.
"""
from __future__ import annotations

from src.collectors.base import BaseCollector, CollectorError, CompanySource, RawJob

SEARCH_URL = "https://www.amazon.jobs/en/search.json"
PAGE_SIZE = 100
MAX_PAGES = 30  # ~3000 most-recent jobs per poll


class AmazonCollector(BaseCollector):
    source_name = "amazon"

    def fetch_jobs(self, company: CompanySource) -> list[RawJob]:
        jobs: list[RawJob] = []
        offset = 0
        for _ in range(MAX_PAGES):
            params = {"offset": offset, "result_limit": PAGE_SIZE, "sort": "recent"}
            data = self.get_json(SEARCH_URL, params=params, _company_name=company.name)

            if not isinstance(data, dict) or "jobs" not in data:
                raise CollectorError(self.source_name, company.name, "unexpected response shape (no 'jobs' key)")

            postings = data["jobs"]
            if not postings:
                break

            for item in postings:
                if not isinstance(item, dict) or "id_icims" not in item or "title" not in item:
                    continue  # skip malformed entries rather than failing the whole board
                job_id = str(item["id_icims"])
                apply_url = item.get("url_next_step") or f"https://www.amazon.jobs{item.get('job_path', '')}"
                description_parts = [
                    item.get("description"),
                    item.get("basic_qualifications"),
                    item.get("preferred_qualifications"),
                ]
                description_html = "\n".join(p for p in description_parts if p) or None
                department = item.get("job_category") or item.get("job_family")
                jobs.append(
                    RawJob(
                        external_id=job_id,
                        title=item.get("title") or "",
                        location_text=item.get("normalized_location"),
                        description_html=description_html,
                        apply_url=apply_url,
                        source_url=apply_url,
                        posted_at=item.get("posted_date"),
                        department=department,
                        raw=item,
                    )
                )

            offset += PAGE_SIZE
            if len(postings) < PAGE_SIZE:
                break

        return jobs

"""Generic collector for permitted, static-HTML company career pages that
don't use a supported ATS.

Only use this for pages whose robots.txt / terms permit automated access —
this project does not scrape LinkedIn or sites that prohibit it. Each
company using this collector must define CSS selectors in
config/companies.yaml under `custom_config`:

    - name: Example Co
      ats_type: custom
      careers_url: https://example.com/careers
      custom_config:
        job_selector: "div.job-listing"      # one element per job
        title_selector: "h3.job-title"       # relative to job_selector
        link_selector: "a.job-link"          # relative to job_selector, href attr
        location_selector: "span.job-location"  # optional, relative to job_selector

This is best-effort: career pages change their markup without notice, so
expect to revisit selectors periodically (see README troubleshooting).
"""
from __future__ import annotations

from urllib.parse import urljoin

from bs4 import BeautifulSoup

from src.collectors.base import BaseCollector, CollectorError, CompanySource, RawJob


class CustomPageCollector(BaseCollector):
    source_name = "custom"

    def fetch_jobs(self, company: CompanySource) -> list[RawJob]:
        if not company.careers_url:
            raise CollectorError(self.source_name, company.name, "missing careers_url")

        config = company.custom_config or {}
        job_selector = config.get("job_selector")
        title_selector = config.get("title_selector")
        link_selector = config.get("link_selector")
        if not job_selector or not title_selector or not link_selector:
            raise CollectorError(
                self.source_name,
                company.name,
                "custom_config must define job_selector, title_selector, and link_selector",
            )
        location_selector = config.get("location_selector")

        html = self.get_text(company.careers_url, _company_name=company.name)
        soup = BeautifulSoup(html, "lxml")

        jobs: list[RawJob] = []
        for element in soup.select(job_selector):
            title_el = element.select_one(title_selector)
            link_el = element.select_one(link_selector)
            if title_el is None or link_el is None or not link_el.get("href"):
                continue  # skip entries that don't match the expected shape

            href = link_el["href"]
            apply_url = urljoin(company.careers_url, href)
            location_text = None
            if location_selector:
                location_el = element.select_one(location_selector)
                if location_el is not None:
                    location_text = location_el.get_text(strip=True)

            title = title_el.get_text(strip=True)
            jobs.append(
                RawJob(
                    external_id=apply_url,  # no stable external id on generic pages; the URL is the identity
                    title=title,
                    location_text=location_text,
                    description_html=None,  # requires a follow-up fetch of the job detail page; left to normalize.py
                    apply_url=apply_url,
                    source_url=company.careers_url,
                    posted_at=None,
                    department=None,
                    raw={"html_snippet": str(element)},
                )
            )
        return jobs

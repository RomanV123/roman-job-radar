"""Ashby public job board collector.

Public endpoint docs: https://developers.ashbyhq.com/docs/public-job-posting-api
    GET https://api.ashbyhq.com/posting-api/job-board/{company}?includeCompensation=true
"""
from __future__ import annotations

from src.collectors.base import BaseCollector, CollectorError, CompanySource, RawJob

BOARD_URL_TEMPLATE = "https://api.ashbyhq.com/posting-api/job-board/{company}?includeCompensation=true"


class AshbyCollector(BaseCollector):
    source_name = "ashby"

    def fetch_jobs(self, company: CompanySource) -> list[RawJob]:
        if not company.board_identifier:
            raise CollectorError(self.source_name, company.name, "missing board_identifier")

        url = BOARD_URL_TEMPLATE.format(company=company.board_identifier)
        data = self.get_json(url, _company_name=company.name)

        if not isinstance(data, dict) or "jobs" not in data:
            raise CollectorError(self.source_name, company.name, "unexpected response shape (no 'jobs' key)")

        jobs: list[RawJob] = []
        for item in data["jobs"]:
            if not isinstance(item, dict) or "id" not in item or "title" not in item:
                continue
            location = item.get("location")
            if isinstance(location, dict):
                location_text = location.get("name")
            else:
                location_text = location
            jobs.append(
                RawJob(
                    external_id=str(item["id"]),
                    title=item.get("title") or "",
                    location_text=location_text,
                    description_html=item.get("descriptionHtml") or item.get("description"),
                    apply_url=item.get("applyUrl") or item.get("jobUrl"),
                    source_url=item.get("jobUrl"),
                    posted_at=item.get("publishedAt"),
                    department=item.get("department"),
                    raw=item,
                )
            )
        return jobs

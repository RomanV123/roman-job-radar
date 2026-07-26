"""Greenhouse public job board collector.

Public endpoint docs: https://developers.greenhouse.io/job-board.html
    GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true
"""
from __future__ import annotations

from src.collectors.base import BaseCollector, CollectorError, CompanySource, RawJob

BOARD_URL_TEMPLATE = "https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true"


class GreenhouseCollector(BaseCollector):
    source_name = "greenhouse"

    def fetch_jobs(self, company: CompanySource) -> list[RawJob]:
        if not company.board_identifier:
            raise CollectorError(self.source_name, company.name, "missing board_identifier")

        url = BOARD_URL_TEMPLATE.format(board_token=company.board_identifier)
        data = self.get_json(url, _company_name=company.name)

        if not isinstance(data, dict) or "jobs" not in data:
            raise CollectorError(self.source_name, company.name, "unexpected response shape (no 'jobs' key)")

        jobs: list[RawJob] = []
        for item in data["jobs"]:
            if not isinstance(item, dict) or "id" not in item or "title" not in item:
                continue  # skip malformed entries rather than failing the whole board
            location = item.get("location") or {}
            departments = item.get("departments") or []
            department_names = [d.get("name") for d in departments if isinstance(d, dict) and d.get("name")]
            jobs.append(
                RawJob(
                    external_id=str(item["id"]),
                    title=item.get("title") or "",
                    location_text=location.get("name"),
                    description_html=item.get("content"),
                    apply_url=item.get("absolute_url"),
                    source_url=item.get("absolute_url"),
                    posted_at=item.get("updated_at"),
                    department=", ".join(department_names) or None,
                    raw=item,
                )
            )
        return jobs

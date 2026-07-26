"""Lever public postings collector.

Public endpoint docs: https://github.com/lever/postings-api
    GET https://api.lever.co/v0/postings/{company}?mode=json
"""
from __future__ import annotations

from src.collectors.base import BaseCollector, CollectorError, CompanySource, RawJob

POSTINGS_URL_TEMPLATE = "https://api.lever.co/v0/postings/{company}?mode=json"


class LeverCollector(BaseCollector):
    source_name = "lever"

    def fetch_jobs(self, company: CompanySource) -> list[RawJob]:
        if not company.board_identifier:
            raise CollectorError(self.source_name, company.name, "missing board_identifier")

        url = POSTINGS_URL_TEMPLATE.format(company=company.board_identifier)
        data = self.get_json(url, _company_name=company.name)

        if not isinstance(data, list):
            raise CollectorError(self.source_name, company.name, "unexpected response shape (expected a list)")

        jobs: list[RawJob] = []
        for item in data:
            if not isinstance(item, dict) or "id" not in item or "text" not in item:
                continue
            categories = item.get("categories") or {}
            created_at = item.get("createdAt")
            jobs.append(
                RawJob(
                    external_id=str(item["id"]),
                    title=item.get("text") or "",
                    location_text=categories.get("location"),
                    description_html=item.get("description") or item.get("descriptionPlain"),
                    apply_url=item.get("applyUrl") or item.get("hostedUrl"),
                    source_url=item.get("hostedUrl"),
                    posted_at=str(created_at) if created_at is not None else None,
                    department=categories.get("team"),
                    raw=item,
                )
            )
        return jobs

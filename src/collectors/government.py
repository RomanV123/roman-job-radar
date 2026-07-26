"""Government / public-sector job feed collector.

Currently supports USAJOBS (https://developer.usajobs.gov/), the only
federal job feed with a documented, stable public JSON API. It requires a
free API key registered to an email address — see README for setup.

State-level agencies (e.g. CalCareers, which several of Roman's target
employers like the California Air Resources Board and California
Department of Technology use) do not currently expose an equivalent public
JSON API. Those should be configured via the `custom` collector once a
specific, scrape-permitted page is identified, or tracked as inactive
review-queue entries in config/companies.yaml until then — this collector
intentionally does not attempt to scrape CalCareers to avoid guessing at
undocumented behavior.
"""
from __future__ import annotations

import os

from src.collectors.base import BaseCollector, CollectorError, CompanySource, RawJob

USAJOBS_SEARCH_URL = "https://data.usajobs.gov/api/search"


class GovernmentCollector(BaseCollector):
    source_name = "government"

    def fetch_jobs(self, company: CompanySource) -> list[RawJob]:
        api = (company.custom_config or {}).get("api", "usajobs")
        if api != "usajobs":
            raise CollectorError(
                self.source_name,
                company.name,
                f"unsupported government api '{api}' (only 'usajobs' is implemented)",
            )
        return self._fetch_usajobs(company)

    def _fetch_usajobs(self, company: CompanySource) -> list[RawJob]:
        api_key = os.environ.get("USAJOBS_API_KEY", "")
        email = os.environ.get("USAJOBS_EMAIL", "")
        if not api_key or not email:
            raise CollectorError(
                self.source_name,
                company.name,
                "USAJOBS_API_KEY and USAJOBS_EMAIL must be set to use the government collector",
            )

        config = company.custom_config or {}
        params: dict[str, str] = {}
        keyword = config.get("keyword") or company.board_identifier
        if keyword:
            params["Keyword"] = keyword
        if config.get("organization"):
            params["Organization"] = config["organization"]
        if config.get("location_name"):
            params["LocationName"] = config["location_name"]
        if config.get("who_may_apply"):
            params["WhoMayApply"] = config["who_may_apply"]

        headers = {
            "Host": "data.usajobs.gov",
            "User-Agent": email,
            "Authorization-Key": api_key,
        }

        data = self.get_json(USAJOBS_SEARCH_URL, params=params, headers=headers, _company_name=company.name)

        if not isinstance(data, dict) or "SearchResult" not in data:
            raise CollectorError(self.source_name, company.name, "unexpected USAJOBS response shape")

        items = data["SearchResult"].get("SearchResultItems", [])
        jobs: list[RawJob] = []
        for item in items:
            match = item.get("MatchedObjectDescriptor")
            if not isinstance(match, dict):
                continue
            positions = match.get("PositionLocation") or []
            location_text = positions[0].get("LocationName") if positions else None
            apply_uri = (match.get("ApplyURI") or [None])[0]
            jobs.append(
                RawJob(
                    external_id=str(item.get("MatchedObjectId") or match.get("PositionID") or ""),
                    title=match.get("PositionTitle") or "",
                    location_text=location_text,
                    description_html=match.get("UserArea", {}).get("Details", {}).get("JobSummary")
                    if isinstance(match.get("UserArea"), dict)
                    else None,
                    apply_url=apply_uri,
                    source_url=match.get("PositionURI"),
                    posted_at=match.get("PublicationStartDate"),
                    department=match.get("DepartmentName"),
                    raw=item,
                )
            )
        return jobs

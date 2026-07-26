"""Shared interface for all job-source collectors.

Every collector (Greenhouse, Lever, Ashby, government feeds, custom career
pages) implements `fetch_jobs(company)` and returns a list of `RawJob`.
Normalization into the `jobs` table schema happens later, in
src/processing/normalize.py — collectors intentionally return source-shaped
data plus the original payload for debugging.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from src.logging_config import get_logger

logger = get_logger(__name__)

USER_AGENT = "RomanJobRadar/1.0 (personal job search tool; contact: rvasilyev@ucdavis.edu)"
DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_MIN_REQUEST_INTERVAL_SECONDS = 0.5


class CollectorError(Exception):
    """Raised when a collector cannot retrieve or parse jobs for a company.

    Always caught at the pipeline level (see safe_collect) so one company's
    failure never stops the rest of the run.
    """

    def __init__(self, source: str, company_name: str, message: str):
        self.source = source
        self.company_name = company_name
        super().__init__(f"[{source}] {company_name}: {message}")


@dataclass
class CompanySource:
    """A company entry loaded from config/companies.yaml."""

    name: str
    ats_type: str  # greenhouse | lever | ashby | government | custom
    industry: str | None = None
    board_identifier: str | None = None
    careers_url: str | None = None
    priority: int = 2
    active: bool = True
    custom_config: dict[str, Any] = field(default_factory=dict)


@dataclass
class RawJob:
    """Source-shaped job data, prior to normalization."""

    external_id: str
    title: str
    location_text: str | None = None
    description_html: str | None = None
    apply_url: str | None = None
    source_url: str | None = None
    posted_at: str | None = None
    department: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class CollectionResult:
    """Outcome of collecting one company's jobs — always produced, even on
    failure, so the pipeline can isolate per-source errors without raising."""

    company_name: str
    source: str
    jobs: list[RawJob]
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    if isinstance(exc, httpx.TransportError):
        return True
    return False


class BaseCollector(ABC):
    source_name: str = "base"

    def __init__(
        self,
        client: httpx.Client | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        min_request_interval: float = DEFAULT_MIN_REQUEST_INTERVAL_SECONDS,
    ):
        self._owns_client = client is None
        self.client = client or httpx.Client(headers={"User-Agent": USER_AGENT})
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.min_request_interval = min_request_interval
        self._last_request_at: float = 0.0

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "BaseCollector":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        wait = self.min_request_interval - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        @retry(
            retry=retry_if_exception(_is_retryable),
            stop=stop_after_attempt(self.max_attempts),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            reraise=True,
        )
        def _do_request() -> httpx.Response:
            self._rate_limit()
            response = self.client.request(method, url, timeout=self.timeout, **kwargs)
            response.raise_for_status()
            return response

        return _do_request()

    def get_json(self, url: str, **kwargs: Any) -> Any:
        company_name = kwargs.pop("_company_name", url)
        try:
            response = self._request("GET", url, **kwargs)
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise CollectorError(self.source_name, company_name, f"HTTP {exc.response.status_code}") from exc
        except httpx.TimeoutException as exc:
            raise CollectorError(self.source_name, company_name, "request timed out") from exc
        except httpx.TransportError as exc:
            raise CollectorError(self.source_name, company_name, f"transport error: {exc}") from exc
        except ValueError as exc:  # json decode error
            raise CollectorError(self.source_name, company_name, f"invalid JSON response: {exc}") from exc

    def get_text(self, url: str, **kwargs: Any) -> str:
        company_name = kwargs.pop("_company_name", url)
        try:
            response = self._request("GET", url, **kwargs)
            return response.text
        except httpx.HTTPStatusError as exc:
            raise CollectorError(self.source_name, company_name, f"HTTP {exc.response.status_code}") from exc
        except httpx.TimeoutException as exc:
            raise CollectorError(self.source_name, company_name, "request timed out") from exc
        except httpx.TransportError as exc:
            raise CollectorError(self.source_name, company_name, f"transport error: {exc}") from exc

    def post_json(self, url: str, json_body: dict[str, Any], **kwargs: Any) -> Any:
        company_name = kwargs.pop("_company_name", url)
        try:
            response = self._request("POST", url, json=json_body, **kwargs)
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise CollectorError(self.source_name, company_name, f"HTTP {exc.response.status_code}") from exc
        except httpx.TimeoutException as exc:
            raise CollectorError(self.source_name, company_name, "request timed out") from exc
        except httpx.TransportError as exc:
            raise CollectorError(self.source_name, company_name, f"transport error: {exc}") from exc
        except ValueError as exc:  # json decode error
            raise CollectorError(self.source_name, company_name, f"invalid JSON response: {exc}") from exc

    @abstractmethod
    def fetch_jobs(self, company: CompanySource) -> list[RawJob]:
        """Fetch and lightly parse jobs for one company. Raise CollectorError
        on failure — do not return a partial list silently."""
        raise NotImplementedError

    def safe_collect(self, company: CompanySource) -> CollectionResult:
        """Never raises. Wraps fetch_jobs so one company's failure can't take
        down a pipeline run across many companies (per-source isolation)."""
        try:
            jobs = self.fetch_jobs(company)
            return CollectionResult(company_name=company.name, source=self.source_name, jobs=jobs)
        except CollectorError as exc:
            logger.warning("Collector failed for %s: %s", company.name, exc)
            return CollectionResult(company_name=company.name, source=self.source_name, jobs=[], error=str(exc))
        except Exception as exc:  # noqa: BLE001 - last-resort isolation boundary
            logger.exception("Unexpected error collecting %s via %s", company.name, self.source_name)
            return CollectionResult(company_name=company.name, source=self.source_name, jobs=[], error=str(exc))


DEFAULT_COMPANIES_PATH = Path("config/companies.yaml")


def load_companies(path: str | Path = DEFAULT_COMPANIES_PATH) -> list[CompanySource]:
    """Load config/companies.yaml into CompanySource objects. Only `active`
    entries with a usable identifier (board_identifier for ATS sources, or
    careers_url for custom/government sources) are returned."""
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    companies: list[CompanySource] = []
    for entry in raw.get("companies", []) or []:
        companies.append(
            CompanySource(
                name=entry["name"],
                ats_type=entry.get("ats_type", "custom"),
                industry=entry.get("industry"),
                board_identifier=entry.get("board_identifier"),
                careers_url=entry.get("careers_url"),
                priority=entry.get("priority", 2),
                active=entry.get("active", True),
                custom_config=entry.get("custom_config", {}) or {},
            )
        )

    active = [c for c in companies if c.active]
    logger.info("Loaded %d companies from %s (%d active)", len(companies), path, len(active))
    return active

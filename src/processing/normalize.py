"""Turns a collector's RawJob into a NormalizedJob ready for the `jobs` table.

Every ATS shapes its data differently, and some (Lever, Ashby) actually
publish clean structured fields for country/workplace-type/employment-type
that are far more reliable than parsing free text — this module prefers
those when the source provides them (confirmed against live API responses)
and falls back to text heuristics only when a source doesn't (Greenhouse
most of the time, Workday, custom pages, government feeds).

Raw source data is never discarded: RawJob.raw is carried through so the
original payload stays available for debugging.
"""
from __future__ import annotations

import hashlib
import re
import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import us
import yaml
from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning
from dateutil import parser as dateutil_parser

warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)

from src.collectors.base import RawJob
from src.logging_config import get_logger

logger = get_logger(__name__)

DEFAULT_TITLE_ALIASES_PATH = Path("config/title_aliases.yaml")

_STATE_NAME_TO_ABBR: dict[str, str] = {s.name.lower(): s.abbr for s in us.states.STATES}
_VALID_STATE_ABBRS: set[str] = {s.abbr for s in us.states.STATES} | {"DC"}

# Countries seen often enough in real job boards to be worth a direct keyword
# check when a source gives us nothing structured (Greenhouse, Workday,
# custom pages). Not exhaustive — an unmatched location is treated as
# "unknown" (is_us=None), not "non-US", so eligibility filtering downstream
# doesn't wrongly reject jobs this heuristic simply doesn't recognize.
_NON_US_COUNTRY_HINTS = [
    # A near-exhaustive list rather than an ad hoc one — a real Bulgaria
    # posting slipped through undetected (and stayed eligible) with an
    # earlier, shorter list that simply hadn't gotten around to Bulgaria.
    # International postings from global employers (Lonza, BigID, etc.)
    # can list almost any country, so this needs to be comprehensive, not
    # reactive.
    "canada", "mexico", "united kingdom", "great britain", "england", "scotland",
    "wales", "northern ireland", "ireland", "germany", "france", "spain", "italy",
    "poland", "netherlands", "sweden", "norway", "denmark", "finland", "iceland",
    "australia", "new zealand", "india", "china", "japan", "singapore",
    "philippines", "brazil", "argentina", "costa rica", "colombia", "chile",
    "peru", "venezuela", "ecuador", "uruguay", "paraguay", "bolivia", "panama",
    "guatemala", "honduras", "el salvador", "nicaragua", "dominican republic",
    "jamaica", "trinidad and tobago", "puerto rico", "cuba", "haiti",
    "south korea", "korea", "north korea", "taiwan", "israel",
    "united arab emirates", "uae", "saudi arabia", "qatar", "kuwait", "bahrain",
    "oman", "jordan", "lebanon", "egypt", "turkey", "iran", "iraq", "pakistan",
    "switzerland", "austria", "belgium", "portugal", "czech republic", "czechia",
    "slovakia", "slovenia", "croatia", "serbia", "bosnia", "montenegro",
    "north macedonia", "albania", "bulgaria", "romania", "hungary", "greece",
    "cyprus", "malta", "luxembourg", "estonia", "latvia", "lithuania", "ukraine",
    "belarus", "russia", "moldova", "georgia", "armenia", "azerbaijan",
    "kazakhstan", "uzbekistan", "vietnam", "thailand", "malaysia", "indonesia",
    "hong kong", "macau", "bangladesh", "sri lanka", "nepal", "myanmar",
    "cambodia", "laos", "mongolia", "south africa", "nigeria", "kenya",
    "ghana", "ethiopia", "morocco", "tunisia", "algeria", "uganda", "tanzania",
    "zimbabwe", "zambia", "rwanda", "senegal", "ivory coast", "cameroon",
    "fiji", "papua new guinea",
]

_US_HINTS = ["united states", "usa", "u.s.", " us)", "(us"]


@dataclass
class LocationInfo:
    display: str | None
    state: str | None  # USPS two-letter abbreviation, if known
    is_us: bool | None  # True / False / None (unknown)


@dataclass
class NormalizedJob:
    external_id: str
    source: str
    title: str
    normalized_title: str
    description: str | None
    location: str | None
    state: str | None
    is_us: bool | None  # True/False/None(unknown) — NOT a jobs-table column; the
    # `jobs` schema has no country field, so this rides on the in-memory
    # NormalizedJob for Phase 7 eligibility filtering to consume before
    # persistence, using the richer per-source signal (e.g. Lever's ISO
    # country code) that isn't otherwise kept once a Job row is saved.
    workplace_type: str | None  # onsite | hybrid | remote | None
    employment_type: str | None  # full_time | internship | contract | part_time | None
    salary_min: float | None
    salary_max: float | None
    experience_min: float | None
    experience_max: float | None
    posted_at: datetime | None
    apply_url: str | None
    source_url: str | None
    content_hash: str


# ---------- Title ----------

def load_title_aliases(path: str | Path = DEFAULT_TITLE_ALIASES_PATH) -> dict[str, str]:
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return dict(raw)


def normalize_title(raw_title: str, aliases: dict[str, str]) -> str:
    title = (raw_title or "").strip()
    lower_title = title.lower()
    for alias, canonical in aliases.items():
        if alias.lower() in lower_title:
            return canonical
    return title


# ---------- HTML cleaning ----------

def clean_html(html: str | None) -> str | None:
    if not html:
        return None
    text = BeautifulSoup(html, "lxml").get_text(separator=" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


# ---------- Location / state / country ----------

def _match_us_state(text: str) -> str | None:
    if not text:
        return None
    for abbr in re.findall(r"\b([A-Z]{2})\b", text):
        if abbr in _VALID_STATE_ABBRS:
            return abbr
    lower = text.lower()
    for name, abbr in _STATE_NAME_TO_ABBR.items():
        if re.search(rf"\b{re.escape(name)}\b", lower):
            return abbr
    return None


def _looks_non_us(text: str) -> bool:
    lower = text.lower()
    return any(hint in lower for hint in _NON_US_COUNTRY_HINTS)


def _looks_us(text: str) -> bool:
    lower = text.lower()
    return any(hint in lower for hint in _US_HINTS)


def _location_from_text(location_text: str | None) -> LocationInfo:
    """Generic fallback used when a source has no structured country/state
    data (Greenhouse, Workday, custom pages)."""
    if not location_text:
        return LocationInfo(display=None, state=None, is_us=None)

    state = _match_us_state(location_text)
    if state:
        return LocationInfo(display=location_text, state=state, is_us=True)
    if _looks_non_us(location_text):
        return LocationInfo(display=location_text, state=None, is_us=False)
    if _looks_us(location_text):
        return LocationInfo(display=location_text, state=None, is_us=True)
    return LocationInfo(display=location_text, state=None, is_us=None)


def _location_from_lever(raw: dict[str, Any], location_text: str | None) -> LocationInfo:
    country = raw.get("country")  # ISO alpha-2, e.g. "US" — confirmed via live API
    if country:
        is_us = country == "US"
        state = _match_us_state(location_text or "") if is_us else None
        return LocationInfo(display=location_text, state=state, is_us=is_us)
    return _location_from_text(location_text)


def _location_from_ashby(raw: dict[str, Any], location_text: str | None) -> LocationInfo:
    address = (raw.get("address") or {}).get("postalAddress") or {}
    # Ashby is inconsistent across companies: some send the full country name
    # ("United States"), others send the ISO code ("US") — confirmed by
    # comparing real payloads from different Ashby-hosted boards.
    country = address.get("addressCountry")
    if country:
        normalized_country = country.strip().lower()
        is_us = normalized_country in ("united states", "us", "usa")
        state = None
        if is_us:
            region = address.get("addressRegion")
            if region:
                state = _STATE_NAME_TO_ABBR.get(region.strip().lower()) or _match_us_state(region)
            if not state:
                state = _match_us_state(location_text or "")
        return LocationInfo(display=location_text, state=state, is_us=is_us)
    return _location_from_text(location_text)


def _location_from_greenhouse(raw: dict[str, Any], location_text: str | None) -> LocationInfo:
    # `offices[0].location` is usually a fuller "City, Region, Country" string
    # than the top-level `location.name` — prefer it for country detection.
    offices = raw.get("offices") or []
    office_location = offices[0].get("location") if offices and isinstance(offices[0], dict) else None
    combined = " ".join(filter(None, [location_text, office_location]))
    return _location_from_text(combined or location_text)


def _location_from_government(raw: dict[str, Any], location_text: str | None) -> LocationInfo:
    match = raw.get("MatchedObjectDescriptor") or {}
    positions = match.get("PositionLocation") or []
    if positions and isinstance(positions[0], dict):
        pos = positions[0]
        country_code = pos.get("CountryCode")
        if country_code:
            is_us = country_code == "US"
            state = pos.get("CountrySubDivisionCode") if is_us else None
            if state and state not in _VALID_STATE_ABBRS:
                state = _match_us_state(location_text or "")
            return LocationInfo(display=location_text, state=state, is_us=is_us)
    return _location_from_text(location_text)


def extract_location(raw_job: RawJob, source: str) -> LocationInfo:
    dispatch = {
        "lever": _location_from_lever,
        "ashby": _location_from_ashby,
        "greenhouse": _location_from_greenhouse,
        "government": _location_from_government,
    }
    handler = dispatch.get(source)
    if handler:
        return handler(raw_job.raw, raw_job.location_text)
    return _location_from_text(raw_job.location_text)  # workday, custom


# ---------- Workplace type ----------

_GREENHOUSE_LOCATION_TYPE_MAP = {"on-site": "onsite", "onsite": "onsite", "remote": "remote", "hybrid": "hybrid"}


def extract_workplace_type(raw_job: RawJob, source: str, description_text: str | None = None) -> str | None:
    raw = raw_job.raw

    if source == "lever":
        value = raw.get("workplaceType")
        if value:
            return value.lower()

    if source == "ashby":
        value = raw.get("workplaceType")
        if value:
            return value.lower()
        if raw.get("isRemote") is True:
            return "remote"

    if source == "greenhouse":
        for item in raw.get("metadata") or []:
            if isinstance(item, dict) and item.get("name") == "Location Type" and item.get("value"):
                mapped = _GREENHOUSE_LOCATION_TYPE_MAP.get(str(item["value"]).strip().lower())
                if mapped:
                    return mapped

    combined = f"{raw_job.location_text or ''} {description_text or ''}".lower()
    if "remote" in combined:
        return "remote"
    if "hybrid" in combined:
        return "hybrid"
    if raw_job.location_text:  # we have *some* location signal, default to onsite
        return "onsite"
    return None  # no signal at all — leave unknown rather than guessing


# ---------- Employment type ----------

_LEVER_COMMITMENT_MAP = {
    "full-time": "full_time",
    "internship": "internship",
    "contractor": "contract",
    "fixed-term": "contract",
    "scholarship": "internship",
    "part-time": "part_time",
}

_ASHBY_EMPLOYMENT_MAP = {
    "fulltime": "full_time",
    "intern": "internship",
    "contract": "contract",
    "temporary": "contract",
    "parttime": "part_time",
}


def extract_employment_type(raw_job: RawJob, source: str, description_text: str | None = None) -> str:
    raw = raw_job.raw

    if source == "lever":
        commitment = (raw.get("categories") or {}).get("commitment")
        if commitment:
            mapped = _LEVER_COMMITMENT_MAP.get(commitment.strip().lower())
            if mapped:
                return mapped

    if source == "ashby":
        employment_type = raw.get("employmentType")
        if employment_type:
            mapped = _ASHBY_EMPLOYMENT_MAP.get(employment_type.strip().lower())
            if mapped:
                return mapped

    combined = f"{raw_job.title} {description_text or ''}".lower()
    if re.search(r"\bintern(ship)?\b", combined):
        return "internship"
    if any(k in combined for k in ("contractor", "contract position", "temporary", " temp ")):
        return "contract"
    if "part-time" in combined or "part time" in combined:
        return "part_time"
    return "full_time"


# ---------- Salary ----------

_UNIT_SUFFIX = r"(?:/\s?(?:hr|yr)|\s?per\s?(?:hour|year))?"
_SALARY_RANGE_RE = re.compile(
    rf"\$\s?(\d[\d,]*(?:\.\d+)?)\s?[kK]?{_UNIT_SUFFIX}\s?(?:-|to|–|—)\s?\$?\s?(\d[\d,]*(?:\.\d+)?)\s?[kK]?{_UNIT_SUFFIX}",
    re.IGNORECASE,
)


def _parse_number(raw: str, had_k_suffix: bool) -> float:
    value = float(raw.replace(",", ""))
    return value * 1000 if had_k_suffix else value


def _parse_salary_from_text(text: str | None) -> tuple[float | None, float | None]:
    if not text:
        return None, None
    match = _SALARY_RANGE_RE.search(text)
    if not match:
        return None, None
    raw_low, raw_high = match.group(1), match.group(2)
    snippet = match.group(0).lower()
    had_k = "k" in snippet
    low = _parse_number(raw_low, had_k)
    high = _parse_number(raw_high, had_k)
    if "/hr" in snippet or "per hour" in text.lower() or "hourly" in text.lower():
        low, high = low * 2080, high * 2080  # 40hr/week * 52 weeks, approximate
    return (low, high) if low <= high else (high, low)


def extract_salary(raw_job: RawJob, source: str, description_text: str | None = None) -> tuple[float | None, float | None]:
    if source == "ashby":
        components = ((raw_job.raw.get("compensation") or {}).get("summaryComponents")) or []
        for component in components:
            if component.get("compensationType") == "Salary" and component.get("minValue") is not None:
                low, high = component.get("minValue"), component.get("maxValue") or component.get("minValue")
                if component.get("interval") == "1 HOUR":
                    low, high = low * 2080, high * 2080
                return float(low), float(high)

    text = " ".join(filter(None, [raw_job.title, description_text]))
    return _parse_salary_from_text(text)


# ---------- Experience ----------

_EXPERIENCE_RANGE_RE = re.compile(r"(\d+)\s*(?:-|to|–|—)\s*(\d+)\+?\s*years?", re.IGNORECASE)
_EXPERIENCE_PLUS_RE = re.compile(r"(\d+)\+\s*years?", re.IGNORECASE)


def extract_experience_years(description_text: str | None) -> tuple[float | None, float | None]:
    if not description_text:
        return None, None
    range_match = _EXPERIENCE_RANGE_RE.search(description_text)
    if range_match:
        low, high = float(range_match.group(1)), float(range_match.group(2))
        return (low, high) if low <= high else (high, low)
    plus_match = _EXPERIENCE_PLUS_RE.search(description_text)
    if plus_match:
        low = float(plus_match.group(1))
        return low, None
    return None, None


# ---------- Posted date ----------

_WORKDAY_RELATIVE_RE = re.compile(r"posted\s+(today|yesterday|(\d+)\+?\s*days?\s*ago)", re.IGNORECASE)


def _parse_workday_relative_date(text: str, reference_time: datetime) -> datetime | None:
    match = _WORKDAY_RELATIVE_RE.search(text)
    if not match:
        return None
    phrase = match.group(1).lower()
    if phrase == "today":
        days_ago = 0
    elif phrase == "yesterday":
        days_ago = 1
    else:
        days_ago = int(match.group(2))
    return reference_time - timedelta(days=days_ago)


def parse_posted_date(
    raw_posted_at: str | None, source: str, reference_time: datetime | None = None
) -> datetime | None:
    if not raw_posted_at:
        return None
    reference_time = reference_time or datetime.now(timezone.utc)

    if source == "workday":
        return _parse_workday_relative_date(raw_posted_at, reference_time)

    if source == "lever":
        try:
            millis = int(raw_posted_at)
            return datetime.fromtimestamp(millis / 1000, tz=timezone.utc)
        except (ValueError, TypeError):
            pass

    try:
        parsed = dateutil_parser.parse(raw_posted_at)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (ValueError, TypeError, OverflowError):
        logger.warning("Could not parse posted date %r for source=%s", raw_posted_at, source)
        return None


# ---------- Content hash (used for dedup in Phase 6) ----------

def compute_content_hash(company_name: str, normalized_title: str, location_display: str | None, description_text: str | None) -> str:
    canonical = "|".join(
        [
            company_name.strip().lower(),
            normalized_title.strip().lower(),
            (location_display or "").strip().lower(),
            (description_text or "")[:500].strip().lower(),
        ]
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------- Top-level entry point ----------

def normalize_job(
    raw_job: RawJob,
    source: str,
    company_name: str,
    title_aliases: dict[str, str],
    reference_time: datetime | None = None,
) -> NormalizedJob:
    normalized_title = normalize_title(raw_job.title, title_aliases)
    description = clean_html(raw_job.description_html)
    location_info = extract_location(raw_job, source)
    workplace_type = extract_workplace_type(raw_job, source, description)
    employment_type = extract_employment_type(raw_job, source, description)
    salary_min, salary_max = extract_salary(raw_job, source, description)
    experience_min, experience_max = extract_experience_years(description)
    posted_at = parse_posted_date(raw_job.posted_at, source, reference_time)
    content_hash = compute_content_hash(company_name, normalized_title, location_info.display, description)

    return NormalizedJob(
        external_id=raw_job.external_id,
        source=source,
        title=raw_job.title,
        normalized_title=normalized_title,
        description=description,
        location=location_info.display,
        state=location_info.state,
        is_us=location_info.is_us,
        workplace_type=workplace_type,
        employment_type=employment_type,
        salary_min=salary_min,
        salary_max=salary_max,
        experience_min=experience_min,
        experience_max=experience_max,
        posted_at=posted_at,
        apply_url=raw_job.apply_url,
        source_url=raw_job.source_url,
        content_hash=content_hash,
    )

"""Objective, deterministic eligibility filtering — runs BEFORE semantic/AI
matching (Phase 8). Every rejection is an explicit, explainable rule; no
LLM or fuzzy scoring is involved here.

Distinguishes hard rejects (reasons — the job is hidden) from warnings
(surfaced to the user as "eligibility concerns" per Phase 8's job-card
spec, but not filtered out) for anything the profile can't definitively
rule in or out — e.g. an unknown citizenship-eligibility status shouldn't
silently hide a job the candidate might actually qualify for.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from src.processing.normalize import NormalizedJob, extract_experience_years

DEFAULT_SENIORITY_EXCLUDE_KEYWORDS = ["Senior", "Staff", "Principal", "Lead", "Director", "Manager"]
DEFAULT_MAX_REQUIRED_EXPERIENCE_YEARS = 3
DEFAULT_INCLUDED_EMPLOYMENT_TYPES = {"full_time", "internship"}

_PREFERRED_SECTION_HEADERS = [
    "preferred qualifications", "preferred skills", "nice to have", "nice-to-have",
    "bonus points", "bonus qualifications", "a plus", "what we'd love to see",
    "even better if", "ideally you", "desired qualifications",
]

_INLINE_PREFERRED_KEYWORDS = ["preferred", "nice to have", "a plus", "bonus", "ideally"]

_UNDERGRAD_ONLY_HINTS = [
    "undergraduate students only", "current undergraduate", "must be an undergraduate",
    "rising senior undergraduate", "enrolled as an undergraduate", "undergraduate degree in progress",
    "must not have graduated", "excludes graduate students", "not open to graduate students",
]

_CLEARANCE_HINTS = [
    "security clearance", "active clearance", "ts/sci", "top secret",
    "must obtain and maintain a security clearance", "clearance eligibility",
]
_CITIZENSHIP_HINTS = [
    "u.s. citizen", "us citizen", "united states citizen", "must be a citizen",
    "citizenship required", "no sponsorship",
]


@dataclass
class EligibilityResult:
    eligible: bool
    reasons: list[str] = field(default_factory=list)  # hard-reject reasons
    warnings: list[str] = field(default_factory=list)  # surfaced, not rejected


def split_required_and_preferred(description: str) -> tuple[str, str]:
    """Best-effort split on common section headers. If no preferred-section
    header is found, the whole text is treated as required — we'd rather
    risk being conservative (checking a bit of preferred text too) than
    miss a real requirement hidden in unstructured text."""
    lower = description.lower()
    positions = [lower.find(h) for h in _PREFERRED_SECTION_HEADERS if h in lower]
    positions = [p for p in positions if p != -1]
    if positions:
        split_at = min(positions)
        return description[:split_at], description[split_at:]
    return description, ""


def extract_required_experience_min(description: str | None) -> float | None:
    """Minimum years of experience stated as a REQUIREMENT (not preferred).
    Layers two heuristics: split off any dedicated 'preferred' section, then
    additionally drop individual sentences that say 'preferred' inline
    (many postings don't use a separate header)."""
    if not description:
        return None

    required_text, _ = split_required_and_preferred(description)
    sentences = re.split(r"(?<=[.!?])\s+|\n+", required_text)
    kept = [s for s in sentences if not any(kw in s.lower() for kw in _INLINE_PREFERRED_KEYWORDS)]
    filtered_text = " ".join(kept)

    low, _high = extract_experience_years(filtered_text)
    return low


# ---------- Individual checks ----------

_SENIORITY_ABBREVIATIONS = {
    "senior": [r"sr\.?"],
    "director": [r"dir\.?"],
    "manager": [r"mgr\.?"],
}


def check_seniority(
    title: str, exclude_keywords: list[str] | None = None, manually_enabled: set[str] | None = None
) -> str | None:
    if manually_enabled and "senior" in manually_enabled:
        return None
    keywords = exclude_keywords or DEFAULT_SENIORITY_EXCLUDE_KEYWORDS
    for keyword in keywords:
        # Real postings often abbreviate ("Sr. Services Delivery Engineer"
        # instead of "Senior ...") — caught via live testing where a "Sr."
        # title slipped past a check that only matched the spelled-out word.
        variants = [re.escape(keyword)] + _SENIORITY_ABBREVIATIONS.get(keyword.lower(), [])
        pattern = r"\b(?:" + "|".join(variants) + r")\b"
        if re.search(pattern, title, re.IGNORECASE):
            return f"Title contains excluded seniority keyword: {keyword!r}"
    return None


def check_required_experience(
    description: str | None, max_years: float = DEFAULT_MAX_REQUIRED_EXPERIENCE_YEARS
) -> str | None:
    required_min = extract_required_experience_min(description)
    if required_min is not None and required_min > max_years:
        return f"Requires {required_min:g}+ years of experience (max configured: {max_years:g})"
    return None


def check_country(is_us: bool | None) -> str | None:
    if is_us is False:
        return "Location is outside the United States"
    return None


def check_employment_type(
    employment_type: str | None,
    included: set[str] | None = None,
    manually_enabled: set[str] | None = None,
) -> str | None:
    included = included or DEFAULT_INCLUDED_EMPLOYMENT_TYPES
    manually_enabled = manually_enabled or set()
    if employment_type is None:
        return None  # unknown — don't reject on missing data
    if employment_type in included or employment_type in manually_enabled:
        return None
    return f"Employment type {employment_type!r} is not full-time or internship"


def check_internship_excludes_graduates(
    employment_type: str | None, description: str | None, is_graduate_student: bool
) -> str | None:
    if employment_type != "internship" or not is_graduate_student or not description:
        return None
    lower = description.lower()
    if any(hint in lower for hint in _UNDERGRAD_ONLY_HINTS):
        return "Internship explicitly excludes graduate students"
    return None


def check_clearance_and_citizenship(
    description: str | None, citizenship_restricted_roles_eligible: bool | None
) -> tuple[str | None, str | None]:
    """Returns (reason, warning). Only rejects when the profile explicitly
    says this candidate does NOT qualify; an unknown/unset profile value
    produces a warning instead, so the job stays visible for the candidate
    to judge for themselves."""
    if not description:
        return None, None
    lower = description.lower()
    mentions_restriction = any(hint in lower for hint in _CLEARANCE_HINTS) or any(
        hint in lower for hint in _CITIZENSHIP_HINTS
    )
    if not mentions_restriction:
        return None, None
    if citizenship_restricted_roles_eligible is False:
        return "Requires clearance/citizenship the profile marks as ineligible", None
    return None, "Job may require a security clearance or U.S. citizenship — verify your eligibility"


def check_expired(is_active: bool) -> str | None:
    if not is_active:
        return "Listing has been marked expired/inactive"
    return None


def check_lookback(
    posted_at: datetime | None, lookback_days: int, reference_time: datetime | None = None
) -> str | None:
    if posted_at is None:
        return None  # unknown posting date — don't reject on missing data
    reference_time = reference_time or datetime.now(timezone.utc)
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=timezone.utc)
    age = reference_time - posted_at
    if age > timedelta(days=lookback_days):
        return f"Posted {age.days} days ago, older than the {lookback_days}-day lookback window"
    return None


# ---------- Combined evaluation ----------

def evaluate_eligibility(
    normalized_job: NormalizedJob,
    profile: Any,  # src.resume.profile_builder.CandidateProfile
    is_active: bool = True,
    max_required_experience_years: float = DEFAULT_MAX_REQUIRED_EXPERIENCE_YEARS,
    seniority_exclude_keywords: list[str] | None = None,
    manually_enabled_categories: set[str] | None = None,
    lookback_days: int = 30,
    reference_time: datetime | None = None,
) -> EligibilityResult:
    manually_enabled = manually_enabled_categories or set()
    eligibility_profile = profile.eligibility if hasattr(profile, "eligibility") else {}
    is_graduate_student = bool(eligibility_profile.get("graduate_student_status", False))
    citizenship_eligible = eligibility_profile.get("citizenship_restricted_roles_eligible")

    reasons: list[str] = []
    warnings: list[str] = []

    for reason in (
        check_seniority(normalized_job.title, seniority_exclude_keywords, manually_enabled),
        check_required_experience(normalized_job.description, max_required_experience_years),
        check_country(normalized_job.is_us),
        check_employment_type(normalized_job.employment_type, manually_enabled=manually_enabled),
        check_internship_excludes_graduates(
            normalized_job.employment_type, normalized_job.description, is_graduate_student
        ),
        check_expired(is_active),
        check_lookback(normalized_job.posted_at, lookback_days, reference_time),
    ):
        if reason:
            reasons.append(reason)

    clearance_reason, clearance_warning = check_clearance_and_citizenship(
        normalized_job.description, citizenship_eligible
    )
    if clearance_reason:
        reasons.append(clearance_reason)
    if clearance_warning:
        warnings.append(clearance_warning)

    return EligibilityResult(eligible=len(reasons) == 0, reasons=reasons, warnings=warnings)

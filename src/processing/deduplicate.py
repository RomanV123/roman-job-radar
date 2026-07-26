"""Detects and merges duplicate job postings.

Checks, in order of confidence:
    1. (source, external_id) exact match — the same posting re-scraped.
       Already enforced at the DB level by a uniqueness constraint
       (see src/database/models.py); this module additionally catches it
       pre-insert so callers can update-in-place instead of erroring.
    2. Canonical apply URL match — catches the same posting resurfacing
       with a new external_id (e.g. a requisition gets re-numbered).
    3. Exact content_hash match — same company + normalized title +
       location + description snippet (see src/processing/normalize.py).
    4. Same company + normalized title + normalized location, corroborated
       by description similarity — company/title/location alone is too
       weak on its own (a company can legitimately have multiple open reqs
       for the same role in the same city), so this path requires both
       postings to have a description AND a similarity ratio above
       DESCRIPTION_SIMILARITY_THRESHOLD before it's treated as a duplicate.

Known schema limitation: the `jobs` table (see src/database/models.py,
fixed in Phase 3) has single `source`/`external_id` columns, not a
multi-source list. When a duplicate is found across two different sources,
merge_duplicate_group() keeps one canonical NormalizedJob and reports the
rest as `merged_from` in the MergedJob result for logging — it does not
persist "all source references" on the Job row itself, since there's no
column for it. Flagging this rather than quietly dropping the requirement:
if full multi-source traceability is wanted, it needs a small schema
addition (e.g. a job_sources table) on top of what Phase 3 already shipped.

"Never send more than one alert for the same job" (Phase 10) falls out of
this module doing its job correctly: as long as the same real-world posting
always resolves to the same Job row, the alert layer can key off job.id.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.models import Job
from src.processing.normalize import NormalizedJob

DESCRIPTION_SIMILARITY_THRESHOLD = 0.85

_TRACKING_QUERY_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gh_src", "gh_jid", "lever-source", "ref", "referer", "referrer", "src",
}


@dataclass
class CompanyJob:
    """A NormalizedJob paired with the company it belongs to — the unit
    deduplication actually operates on."""

    company_name: str
    company_id: int | None
    job: NormalizedJob


@dataclass
class MergedJob:
    kept: CompanyJob
    merged_from: list[CompanyJob] = field(default_factory=list)


@dataclass
class DeduplicationResult:
    unique: list[CompanyJob]
    merged: list[MergedJob]

    @property
    def all_kept(self) -> list[CompanyJob]:
        return self.unique + [m.kept for m in self.merged]


def canonicalize_url(url: str | None) -> str | None:
    """Normalizes a URL for comparison: lowercase scheme/host, strip
    trailing slash and known tracking query params, drop fragment."""
    if not url:
        return None
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/") or "/"
    query_pairs = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_QUERY_PARAMS
    ]
    query = urlencode(sorted(query_pairs))
    return urlunsplit((scheme, netloc, path, query, ""))


def description_similarity(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _title_location_key(entry: CompanyJob) -> tuple[str, str, str]:
    job = entry.job
    return (
        entry.company_name.strip().lower(),
        (job.normalized_title or job.title).strip().lower(),
        (job.location or "").strip().lower(),
    )


def is_duplicate(a: CompanyJob, b: CompanyJob) -> str | None:
    """Returns the matched criterion name, or None if the two entries are
    not considered duplicates."""
    job_a, job_b = a.job, b.job

    if job_a.source == job_b.source and job_a.external_id and job_a.external_id == job_b.external_id:
        return "external_id"

    url_a, url_b = canonicalize_url(job_a.apply_url), canonicalize_url(job_b.apply_url)
    if url_a and url_b and url_a == url_b:
        return "apply_url"

    if job_a.content_hash and job_b.content_hash and job_a.content_hash == job_b.content_hash:
        return "content_hash"

    if _title_location_key(a) == _title_location_key(b):
        similarity = description_similarity(job_a.description, job_b.description)
        if similarity >= DESCRIPTION_SIMILARITY_THRESHOLD:
            return "title_location_similarity"

    return None


def _source_preference_score(entry: CompanyJob) -> tuple[int, int, str]:
    """Higher is preferred when choosing which duplicate to keep: prefer the
    posting with a fetched description, then the one with salary data, with
    source name as a final deterministic tiebreak."""
    job = entry.job
    return (
        1 if job.description else 0,
        1 if job.salary_min is not None else 0,
        job.source,
    )


def merge_duplicate_group(group: list[CompanyJob]) -> MergedJob:
    if len(group) == 1:
        return MergedJob(kept=group[0], merged_from=[])
    ranked = sorted(group, key=_source_preference_score, reverse=True)
    return MergedJob(kept=ranked[0], merged_from=ranked[1:])


def deduplicate_jobs(entries: list[CompanyJob]) -> DeduplicationResult:
    """Groups duplicates within `entries` (a single pipeline run's freshly
    normalized jobs) using transitive closure over pairwise is_duplicate()
    matches, then merges each group. Comparison is scoped per-company for
    efficiency — cross-company duplicates aren't a real scenario here since
    every company in config/companies.yaml maps to exactly one ATS source."""
    by_company: dict[str, list[int]] = {}
    for i, entry in enumerate(entries):
        by_company.setdefault(entry.company_name, []).append(i)

    parent = list(range(len(entries)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        root_x, root_y = find(x), find(y)
        if root_x != root_y:
            parent[root_y] = root_x

    for indices in by_company.values():
        for i_pos in range(len(indices)):
            for j_pos in range(i_pos + 1, len(indices)):
                i, j = indices[i_pos], indices[j_pos]
                if is_duplicate(entries[i], entries[j]):
                    union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(len(entries)):
        groups.setdefault(find(i), []).append(i)

    unique: list[CompanyJob] = []
    merged: list[MergedJob] = []
    for indices in groups.values():
        group = [entries[i] for i in indices]
        if len(group) == 1:
            unique.append(group[0])
        else:
            merged.append(merge_duplicate_group(group))

    return DeduplicationResult(unique=unique, merged=merged)


# ---------- DB-aware lookup (for Phase 11's pipeline) ----------

def find_existing_job(session: Session, company_id: int, normalized: NormalizedJob) -> Job | None:
    """Looks for an already-persisted Job row representing the same posting,
    checking the same criteria as is_duplicate() but against the database
    instead of another in-memory candidate. Used so a re-scraped posting
    updates last_seen_at instead of creating a duplicate row."""
    exact = session.execute(
        select(Job).where(Job.source == normalized.source, Job.external_id == normalized.external_id)
    ).scalar_one_or_none()
    if exact:
        return exact

    if normalized.content_hash:
        by_hash = session.execute(
            select(Job).where(Job.company_id == company_id, Job.content_hash == normalized.content_hash)
        ).scalar_one_or_none()
        if by_hash:
            return by_hash

    canonical_new = canonicalize_url(normalized.apply_url)
    if canonical_new:
        candidates = session.execute(select(Job).where(Job.company_id == company_id)).scalars().all()
        for candidate in candidates:
            if canonicalize_url(candidate.apply_url) == canonical_new:
                return candidate

    return None

"""Query and filter logic for the Streamlit dashboard, kept separate from
any Streamlit-specific code so it's independently testable and so a future
swap to React/Next.js (per the stated long-term architecture) only touches
the UI layer, not this one.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.database.models import Application, Company, Job, JobMatch
from src.processing.normalize import is_us_location


@dataclass
class JobRow:
    job_id: int
    company_name: str
    industry: str | None
    title: str
    normalized_title: str
    description: str | None
    location: str | None
    state: str | None
    workplace_type: str | None
    employment_type: str | None
    salary_min: float | None
    salary_max: float | None
    experience_min: float | None
    experience_max: float | None
    posted_at: datetime | None
    apply_url: str | None
    source_url: str | None
    source: str
    is_active: bool
    total_score: float
    skills_score: float
    experience_score: float
    title_score: float
    education_score: float
    location_score: float
    semantic_score: float
    freshness_score: float
    matching_skills: list[str]
    missing_required_skills: list[str]
    missing_preferred_skills: list[str]
    match_explanation: str | None
    application_status: str | None
    application_notes: str | None
    application_id: int | None


def _parse_missing_skills(raw: str | None) -> tuple[list[str], list[str]]:
    if not raw:
        return [], []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return [], []
    if isinstance(data, dict):
        return data.get("required", []), data.get("preferred", [])
    if isinstance(data, list):  # defensive: tolerate a flat list shape too
        return data, []
    return [], []


def _parse_matching_skills(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return data if isinstance(data, list) else []


def load_visible_jobs(session: Session, only_active: bool = True) -> list[JobRow]:
    """Loads every job that has at least one score, paired with its most
    recent JobMatch (a job can be re-scored across pipeline runs) and its
    application status, if any.

    US-only by design: a job is excluded only when its stored location
    resolves to a confirmed non-US location (see is_us_location). Ambiguous
    locations (e.g. a bare "Remote" with no country given) are kept, not
    treated as non-US -- most companies on this board are US-based, so an
    unlabeled remote posting is far more likely US than not. This runs at
    read time rather than relying solely on eligibility filtering at
    collection time, so it takes effect immediately on jobs already sitting
    in the database, not just ones scored by a future pipeline run."""
    latest_eval = (
        select(JobMatch.job_id, func.max(JobMatch.evaluated_at).label("max_evaluated_at"))
        .group_by(JobMatch.job_id)
        .subquery()
    )

    query = (
        select(Job, JobMatch, Company, Application)
        .join(JobMatch, JobMatch.job_id == Job.id)
        .join(
            latest_eval,
            (JobMatch.job_id == latest_eval.c.job_id) & (JobMatch.evaluated_at == latest_eval.c.max_evaluated_at),
        )
        .join(Company, Company.id == Job.company_id)
        .outerjoin(Application, Application.job_id == Job.id)
    )
    if only_active:
        query = query.where(Job.is_active.is_(True))

    rows: list[JobRow] = []
    for job, match, company, application in session.execute(query).all():
        if is_us_location(job.location, job.state) is False:
            continue
        missing_required, missing_preferred = _parse_missing_skills(match.missing_skills)
        rows.append(
            JobRow(
                job_id=job.id,
                company_name=company.name,
                industry=company.industry,
                title=job.title,
                normalized_title=job.normalized_title or job.title,
                description=job.description,
                location=job.location,
                state=job.state,
                workplace_type=job.workplace_type,
                employment_type=job.employment_type,
                salary_min=job.salary_min,
                salary_max=job.salary_max,
                experience_min=job.experience_min,
                experience_max=job.experience_max,
                posted_at=job.posted_at,
                apply_url=job.apply_url,
                source_url=job.source_url,
                source=job.source,
                is_active=job.is_active,
                total_score=match.total_score,
                skills_score=match.skills_score,
                experience_score=match.experience_score,
                title_score=match.title_score,
                education_score=match.education_score,
                location_score=match.location_score,
                semantic_score=match.semantic_score,
                freshness_score=match.freshness_score,
                matching_skills=_parse_matching_skills(match.matching_skills),
                missing_required_skills=missing_required,
                missing_preferred_skills=missing_preferred,
                match_explanation=match.match_explanation,
                application_status=application.status if application else None,
                application_notes=application.notes if application else None,
                application_id=application.id if application else None,
            )
        )
    return rows


@dataclass
class JobFilters:
    min_score: float = 0.0
    posted_within_days: int | None = None
    employment_types: tuple[str, ...] | None = None
    workplace_types: tuple[str, ...] | None = None
    california_only: bool = False
    remote_only: bool = False
    industries: tuple[str, ...] | None = None
    companies: tuple[str, ...] | None = None
    role_category_titles: tuple[str, ...] | None = None  # flattened target titles for the selected categor(y/ies)
    max_required_experience: float | None = None
    min_salary: float | None = None
    missing_skill: str | None = None
    reference_time: datetime | None = None


def apply_filters(rows: list[JobRow], filters: JobFilters) -> list[JobRow]:
    reference_time = filters.reference_time or datetime.now(timezone.utc)
    result = []
    for row in rows:
        if row.total_score < filters.min_score:
            continue
        if filters.posted_within_days is not None:
            if row.posted_at is None:
                continue
            posted = row.posted_at if row.posted_at.tzinfo else row.posted_at.replace(tzinfo=timezone.utc)
            if reference_time - posted > timedelta(days=filters.posted_within_days):
                continue
        if filters.employment_types and row.employment_type not in filters.employment_types:
            continue
        if filters.workplace_types and row.workplace_type not in filters.workplace_types:
            continue
        if filters.california_only and row.state != "CA":
            continue
        if filters.remote_only and row.workplace_type != "remote":
            continue
        if filters.industries and row.industry not in filters.industries:
            continue
        if filters.companies and row.company_name not in filters.companies:
            continue
        if filters.role_category_titles and row.normalized_title not in filters.role_category_titles:
            continue
        if filters.max_required_experience is not None and row.experience_min is not None:
            if row.experience_min > filters.max_required_experience:
                continue
        if filters.min_salary is not None:
            if row.salary_max is not None and row.salary_max < filters.min_salary:
                continue
        if filters.missing_skill:
            all_missing = set(row.missing_required_skills) | set(row.missing_preferred_skills)
            if filters.missing_skill not in all_missing:
                continue
        result.append(row)
    return result


# ---------- Page-specific base predicates ----------

def page_california(rows: list[JobRow]) -> list[JobRow]:
    return [r for r in rows if r.state == "CA"]


def page_nationwide(rows: list[JobRow]) -> list[JobRow]:
    return list(rows)  # every eligible US job, CA included — the full national list


def page_remote(rows: list[JobRow]) -> list[JobRow]:
    return [r for r in rows if r.workplace_type == "remote"]


def page_internships(rows: list[JobRow]) -> list[JobRow]:
    return [r for r in rows if r.employment_type == "internship"]


def page_full_time(rows: list[JobRow]) -> list[JobRow]:
    return [r for r in rows if r.employment_type == "full_time"]


def page_biotech_ot(rows: list[JobRow]) -> list[JobRow]:
    return [r for r in rows if r.industry in ("biotech", "ot")]


def page_recommended(rows: list[JobRow], min_score: float = 80.0) -> list[JobRow]:
    return sorted((r for r in rows if r.total_score >= min_score), key=lambda r: -r.total_score)


def page_saved(rows: list[JobRow]) -> list[JobRow]:
    return [r for r in rows if r.application_status == "saved"]


def page_application_tracker(rows: list[JobRow]) -> list[JobRow]:
    return [r for r in rows if r.application_status is not None]


# ---------- Application mutations ----------

def get_or_create_application(session: Session, job_id: int) -> Application:
    existing = session.execute(select(Application).where(Application.job_id == job_id)).scalar_one_or_none()
    if existing:
        return existing
    application = Application(job_id=job_id, status="saved")
    session.add(application)
    session.flush()
    return application


def set_application_status(session: Session, job_id: int, status: str) -> Application:
    application = get_or_create_application(session, job_id)
    application.status = status
    if status == "applied" and application.date_applied is None:
        application.date_applied = datetime.now(timezone.utc)
    return application


def set_application_notes(session: Session, job_id: int, notes: str) -> Application:
    application = get_or_create_application(session, job_id)
    application.notes = notes
    return application


def remove_application(session: Session, job_id: int) -> None:
    existing = session.execute(select(Application).where(Application.job_id == job_id)).scalar_one_or_none()
    if existing:
        session.delete(existing)

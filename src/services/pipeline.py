"""Orchestrates one end-to-end pipeline run: collect -> normalize ->
deduplicate -> eligibility filter -> score -> persist.

This is the shared core the dashboard's data comes from. It's also what
Phase 11's run_pipeline.py CLI (scheduling, --dry-run, --source/--company
flags, cron/GitHub Actions wiring) will call into — built now because the
Streamlit dashboard needs real, persisted jobs to actually demonstrate.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml
from sqlalchemy import select

from src.alerts import AlertStats, Notification, ScoredJobForAlert, get_alert_provider, send_job_alerts
from src.collectors import CompanySource, get_collector_class, load_companies
from src.database.models import Company, Job, JobMatch, PipelineRun
from src.database.session import get_session
from src.logging_config import get_logger
from src.matching.explanation import build_match_presentation
from src.matching.scorer import score_job
from src.processing.deduplicate import CompanyJob, deduplicate_jobs, find_existing_job
from src.processing.eligibility import evaluate_eligibility
from src.processing.expire_jobs import expire_missing_jobs, expire_stale_jobs
from src.processing.normalize import load_title_aliases, normalize_job
from src.resume.profile_builder import build_alias_lookup, load_profile, load_skill_dictionary
from src.services.job_board_export import run_export as export_job_board
from src.settings import get_settings

logger = get_logger(__name__)

DOMAIN_SETTINGS_PATH = "config/settings.yaml"
REPEATED_FAILURE_THRESHOLD = 3  # consecutive zero-progress runs before alerting
JOB_BOARD_EXPORT_DIR = Path("../roman-job-radar-board")


@dataclass
class PipelineStats:
    companies_processed: int = 0
    companies_failed: int = 0
    jobs_found: int = 0
    new_jobs: int = 0
    updated_jobs: int = 0
    eligible_jobs: int = 0
    scored_jobs: int = 0
    expired_jobs: int = 0
    errors: list[str] = field(default_factory=list)
    alert_stats: AlertStats | None = None
    repeated_failure_alert_sent: bool = False


def check_repeated_failures(session, threshold: int = REPEATED_FAILURE_THRESHOLD) -> bool:
    """A run "fails" here if it recorded errors and collected zero jobs —
    a couple of individual company failures per run is normal (per-source
    isolation is the whole point of Phase 4); this only fires when the
    pipeline has made literally no progress for several runs in a row."""
    recent_runs = (
        session.execute(select(PipelineRun).order_by(PipelineRun.started_at.desc()).limit(threshold))
        .scalars()
        .all()
    )
    if len(recent_runs) < threshold:
        return False
    return all(run.errors and run.jobs_found == 0 for run in recent_runs)


def load_domain_settings(path: str = DOMAIN_SETTINGS_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_or_create_company(session, company_source: CompanySource) -> Company:
    existing = session.execute(select(Company).where(Company.name == company_source.name)).scalar_one_or_none()
    if existing:
        return existing
    company = Company(
        name=company_source.name,
        industry=company_source.industry,
        ats_type=company_source.ats_type,
        board_identifier=company_source.board_identifier,
        careers_url=company_source.careers_url,
        priority=company_source.priority,
        active=company_source.active,
    )
    session.add(company)
    session.flush()
    return company


def _apply_job_fields(job_row: Job, normalized) -> None:
    job_row.title = normalized.title
    job_row.normalized_title = normalized.normalized_title
    job_row.description = normalized.description
    job_row.location = normalized.location
    job_row.state = normalized.state
    job_row.workplace_type = normalized.workplace_type
    job_row.employment_type = normalized.employment_type
    job_row.salary_min = normalized.salary_min
    job_row.salary_max = normalized.salary_max
    job_row.experience_min = normalized.experience_min
    job_row.experience_max = normalized.experience_max
    job_row.posted_at = normalized.posted_at
    job_row.apply_url = normalized.apply_url
    job_row.source_url = normalized.source_url
    job_row.content_hash = normalized.content_hash


def run_pipeline(
    company_names: list[str] | None = None,
    source_filter: str | None = None,
    dry_run: bool = False,
    reference_time: datetime | None = None,
    jobs_per_company_limit: int | None = None,
) -> PipelineStats:
    """Runs the full pipeline. `jobs_per_company_limit` exists for
    development/demo runs so seeding a dashboard doesn't have to pull
    every one of Thermo Fisher's 3000+ postings; production runs (Phase 11)
    should leave it unset."""
    reference_time = reference_time or datetime.now(timezone.utc)
    stats = PipelineStats()

    domain_settings = load_domain_settings()
    app_settings = get_settings()
    profile = load_profile()
    skill_dict = load_skill_dictionary()
    alias_lookup = build_alias_lookup(skill_dict)
    title_aliases = load_title_aliases()
    target_titles = [t for cat in domain_settings["target_role_categories"].values() for t in cat]
    skill_combinations = domain_settings.get("skill_combinations_bonus", [])
    manually_enabled = set(domain_settings["employment_types"].get("manually_enabled_categories", []))
    seniority_keywords = domain_settings.get("seniority_exclude_keywords")
    max_required_experience_years = domain_settings.get("max_required_experience_years", 3)
    lookback_days = app_settings.search_lookback_days

    companies = load_companies()
    if company_names:
        wanted = {n.lower() for n in company_names}
        companies = [c for c in companies if c.name.lower() in wanted]
    if source_filter:
        companies = [c for c in companies if c.ats_type == source_filter]

    all_candidates: list[CompanyJob] = []
    company_lookup: dict[str, CompanySource] = {}
    # Only companies scraped in FULL this run are eligible for missing-job
    # expiration — a --limit-truncated run didn't see the whole board, so
    # comparing against it would wrongly expire everything past the limit.
    seen_external_ids_by_company: dict[str, set[str]] = {}

    for company_source in companies:
        company_lookup[company_source.name] = company_source
        cls = get_collector_class(company_source.ats_type)
        try:
            with cls() as collector:
                result = collector.safe_collect(company_source)
        except Exception as exc:  # noqa: BLE001 - per-company isolation boundary
            stats.companies_failed += 1
            stats.errors.append(f"{company_source.name}: {exc}")
            logger.warning("Company %s raised during collection: %s", company_source.name, exc)
            continue

        if not result.ok:
            stats.companies_failed += 1
            stats.errors.append(f"{company_source.name}: {result.error}")
            continue

        stats.companies_processed += 1
        stats.jobs_found += len(result.jobs)

        raw_jobs = result.jobs[:jobs_per_company_limit] if jobs_per_company_limit else result.jobs
        if jobs_per_company_limit is None:
            seen_external_ids_by_company[company_source.name] = {j.external_id for j in raw_jobs}

        for raw_job in raw_jobs:
            normalized = normalize_job(
                raw_job, company_source.ats_type, company_source.name, title_aliases, reference_time
            )
            all_candidates.append(CompanyJob(company_name=company_source.name, company_id=None, job=normalized))

    dedup_result = deduplicate_jobs(all_candidates)

    if dry_run:
        for entry in dedup_result.all_kept:
            eligibility_result = evaluate_eligibility(
                entry.job,
                profile,
                max_required_experience_years=max_required_experience_years,
                seniority_exclude_keywords=seniority_keywords,
                manually_enabled_categories=manually_enabled,
                lookback_days=lookback_days,
                reference_time=reference_time,
            )
            if eligibility_result.eligible:
                stats.eligible_jobs += 1
        return stats

    newly_scored_for_alerts: list[ScoredJobForAlert] = []

    with get_session() as session:
        run = PipelineRun(source=source_filter, started_at=reference_time)
        session.add(run)
        session.flush()

        # A job_id should only ever get one JobMatch per run (evaluated_at is
        # pinned to this run's single reference_time) -- this can otherwise
        # be violated if two distinct entries in dedup_result.all_kept both
        # resolve to the same existing DB row via find_existing_job (seen in
        # practice from a transient duplicate in a live ATS response that
        # slipped past in-run dedup). Guarding here, rather than only
        # relying on dedup to prevent it upstream, stops that from ever
        # reaching a second INSERT attempt.
        scored_job_ids: set[int] = set()

        for entry in dedup_result.all_kept:
            company_source = company_lookup[entry.company_name]
            normalized = entry.job
            try:
                # A SAVEPOINT per entry so one bad record (e.g. an
                # unexpected IntegrityError) only loses that one job's
                # work, not the entire run's -- a real multi-hour run was
                # previously lost in full to a single duplicate-key
                # collision because everything shared one transaction.
                with session.begin_nested():
                    company = get_or_create_company(session, company_source)

                    existing = find_existing_job(session, company.id, normalized)
                    if existing:
                        _apply_job_fields(existing, normalized)
                        existing.last_seen_at = reference_time
                        existing.is_active = True
                        job_row = existing
                        is_new_job = False
                        stats.updated_jobs += 1
                    else:
                        job_row = Job(
                            external_id=normalized.external_id,
                            source=normalized.source,
                            company_id=company.id,
                            first_seen_at=reference_time,
                            last_seen_at=reference_time,
                            is_active=True,
                        )
                        _apply_job_fields(job_row, normalized)
                        session.add(job_row)
                        session.flush()
                        is_new_job = True
                        stats.new_jobs += 1

                    if job_row.id in scored_job_ids:
                        continue

                    eligibility_result = evaluate_eligibility(
                        normalized,
                        profile,
                        is_active=True,
                        max_required_experience_years=max_required_experience_years,
                        seniority_exclude_keywords=seniority_keywords,
                        manually_enabled_categories=manually_enabled,
                        lookback_days=lookback_days,
                        reference_time=reference_time,
                    )
                    if not eligibility_result.eligible:
                        continue
                    stats.eligible_jobs += 1

                    breakdown = score_job(
                        normalized,
                        profile,
                        alias_lookup,
                        skill_combinations=skill_combinations,
                        target_titles=target_titles,
                        lookback_days=lookback_days,
                        reference_time=reference_time,
                    )
                    presentation = build_match_presentation(breakdown, profile, eligibility_result, company_source.name)

                    match_row = JobMatch(
                        job_id=job_row.id,
                        total_score=breakdown.total_score,
                        skills_score=breakdown.skills_score,
                        experience_score=breakdown.experience_score,
                        title_score=breakdown.title_score,
                        education_score=breakdown.education_score,
                        location_score=breakdown.location_score,
                        semantic_score=breakdown.semantic_score,
                        freshness_score=breakdown.freshness_score,
                        matching_skills=presentation.matching_skills_json,
                        missing_skills=presentation.missing_skills_json,
                        match_explanation=presentation.match_explanation,
                        evaluated_at=reference_time,
                    )
                    session.add(match_row)
                    session.flush()  # surface any IntegrityError here, inside this entry's own try/except
                    scored_job_ids.add(job_row.id)
                    stats.scored_jobs += 1

                    # Only jobs CREATED this run get alerted on — a job that
                    # already existed is an update (last_seen_at bump), not
                    # a new discovery, so this naturally prevents
                    # re-alerting across pipeline runs.
                    if is_new_job:
                        newly_scored_for_alerts.append(
                            ScoredJobForAlert(
                                score=breakdown.total_score,
                                title=normalized.title,
                                company=company_source.name,
                                location=normalized.location,
                                apply_url=normalized.apply_url,
                                match_explanation=presentation.match_explanation,
                            )
                        )
            except Exception as exc:  # noqa: BLE001 - per-entry isolation boundary, mirrors per-company collection isolation above
                stats.errors.append(f"{company_source.name} job {normalized.external_id}: {exc}")
                logger.warning(
                    "Failed to persist/score job %s for %s: %s", normalized.external_id, company_source.name, exc
                )

        for company_name, seen_ids in seen_external_ids_by_company.items():
            company_source = company_lookup[company_name]
            company = get_or_create_company(session, company_source)
            stats.expired_jobs += expire_missing_jobs(session, company.id, seen_ids)

        stats.expired_jobs += expire_stale_jobs(session, lookback_days, reference_time)

        run.completed_at = datetime.now(timezone.utc)
        run.jobs_found = stats.jobs_found
        run.new_jobs = stats.new_jobs
        run.errors = json.dumps(stats.errors) if stats.errors else None

    if newly_scored_for_alerts:
        provider = get_alert_provider(app_settings)
        stats.alert_stats = send_job_alerts(
            newly_scored_for_alerts,
            match_alert_threshold=app_settings.match_alert_threshold,
            immediate_alert_threshold=app_settings.immediate_alert_threshold,
            provider=provider,
        )

    with get_session() as check_session:
        if check_repeated_failures(check_session):
            provider = get_alert_provider(app_settings)
            failure_notification = Notification(
                title="Roman Job Radar — pipeline repeatedly failing",
                message=(
                    f"The last {REPEATED_FAILURE_THRESHOLD} pipeline runs all collected zero jobs "
                    "and recorded errors. Check company configuration, network connectivity, "
                    "or whether an ATS changed its API."
                ),
            )
            if provider.send(failure_notification):
                stats.repeated_failure_alert_sent = True

    # Only a full, real run (no --company/--source narrowing, not a
    # --dry-run) has scraped the whole board -- exporting a partial run
    # would make the public job board look like coverage regressed.
    is_full_run = not company_names and not source_filter and not dry_run
    if is_full_run:
        try:
            export_job_board(JOB_BOARD_EXPORT_DIR)
        except Exception as exc:  # noqa: BLE001 - export failure must never fail the pipeline
            logger.warning("Job board export failed: %s", exc)
            stats.errors.append(f"job_board_export: {exc}")

    return stats

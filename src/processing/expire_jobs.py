"""Marks jobs inactive — either because a fresh scrape of their company's
board no longer lists them (the posting was pulled or filled), or because
they've sat untouched past the configured lookback window (a safety net
for jobs a company scrape happened to skip).

Expired jobs aren't deleted — is_active=False just hides them from the
dashboard's default views while keeping history intact.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.models import Job
from src.logging_config import get_logger

logger = get_logger(__name__)


def expire_missing_jobs(session: Session, company_id: int, seen_external_ids: set[str]) -> int:
    """For one company, marks any currently-active job whose external_id
    was NOT present in this run's fresh scrape as inactive. Only call this
    when the scrape actually covered the company's full board — a
    truncated/limited collection run would wrongly expire everything it
    didn't happen to fetch."""
    jobs = session.execute(
        select(Job).where(Job.company_id == company_id, Job.is_active.is_(True))
    ).scalars().all()

    expired = 0
    for job in jobs:
        if job.external_id not in seen_external_ids:
            job.is_active = False
            expired += 1

    if expired:
        logger.info("Expired %d missing job(s) for company_id=%d", expired, company_id)
    return expired


def expire_stale_jobs(session: Session, lookback_days: int, reference_time: datetime | None = None) -> int:
    """Safety net: marks any active job not seen in longer than the
    lookback window as inactive, regardless of company."""
    reference_time = reference_time or datetime.now(timezone.utc)
    cutoff = reference_time - timedelta(days=lookback_days)

    jobs = session.execute(
        select(Job).where(Job.is_active.is_(True), Job.last_seen_at < cutoff)
    ).scalars().all()

    for job in jobs:
        job.is_active = False

    if jobs:
        logger.info("Expired %d stale job(s) older than %d days", len(jobs), lookback_days)
    return len(jobs)

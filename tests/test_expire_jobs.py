from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database.models import Base, Company, Job
from src.processing.expire_jobs import expire_missing_jobs, expire_stale_jobs

REFERENCE_TIME = datetime(2026, 7, 25, tzinfo=timezone.utc)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture
def company(session):
    c = Company(name="Acme", ats_type="greenhouse", board_identifier="acme")
    session.add(c)
    session.commit()
    return c


def make_job(session, company, external_id, is_active=True, last_seen_at=None):
    job = Job(
        external_id=external_id, source="greenhouse", company_id=company.id,
        title="Analyst", is_active=is_active,
        last_seen_at=last_seen_at or REFERENCE_TIME,
    )
    session.add(job)
    session.commit()
    return job


# ---------- expire_missing_jobs ----------

def test_expire_missing_jobs_marks_unseen_job_inactive(session, company):
    still_posted = make_job(session, company, "1")
    pulled_down = make_job(session, company, "2")

    expired_count = expire_missing_jobs(session, company.id, seen_external_ids={"1"})
    session.commit()

    assert expired_count == 1
    session.refresh(still_posted)
    session.refresh(pulled_down)
    assert still_posted.is_active is True
    assert pulled_down.is_active is False


def test_expire_missing_jobs_leaves_already_inactive_alone(session, company):
    already_inactive = make_job(session, company, "1", is_active=False)
    expire_missing_jobs(session, company.id, seen_external_ids=set())
    session.commit()
    session.refresh(already_inactive)
    assert already_inactive.is_active is False  # untouched, not double-counted


def test_expire_missing_jobs_scoped_to_company(session, company):
    other_company = Company(name="Other Co", ats_type="lever", board_identifier="other")
    session.add(other_company)
    session.commit()
    other_job = make_job(session, other_company, "1")

    expire_missing_jobs(session, company.id, seen_external_ids=set())
    session.commit()

    session.refresh(other_job)
    assert other_job.is_active is True  # different company, not touched


def test_expire_missing_jobs_no_expiry_when_all_seen(session, company):
    job1 = make_job(session, company, "1")
    job2 = make_job(session, company, "2")
    expired_count = expire_missing_jobs(session, company.id, seen_external_ids={"1", "2"})
    assert expired_count == 0


# ---------- expire_stale_jobs ----------

def test_expire_stale_jobs_marks_old_jobs_inactive(session, company):
    old_job = make_job(session, company, "1", last_seen_at=REFERENCE_TIME - timedelta(days=45))
    recent_job = make_job(session, company, "2", last_seen_at=REFERENCE_TIME - timedelta(days=2))

    expired_count = expire_stale_jobs(session, lookback_days=30, reference_time=REFERENCE_TIME)
    session.commit()

    assert expired_count == 1
    session.refresh(old_job)
    session.refresh(recent_job)
    assert old_job.is_active is False
    assert recent_job.is_active is True


def test_expire_stale_jobs_across_all_companies(session, company):
    other_company = Company(name="Other Co", ats_type="lever", board_identifier="other")
    session.add(other_company)
    session.commit()
    old_job_other = make_job(session, other_company, "1", last_seen_at=REFERENCE_TIME - timedelta(days=99))

    expired_count = expire_stale_jobs(session, lookback_days=30, reference_time=REFERENCE_TIME)
    session.commit()

    assert expired_count == 1
    session.refresh(old_job_other)
    assert old_job_other.is_active is False


def test_expire_stale_jobs_none_when_all_recent(session, company):
    make_job(session, company, "1", last_seen_at=REFERENCE_TIME - timedelta(days=1))
    assert expire_stale_jobs(session, lookback_days=30, reference_time=REFERENCE_TIME) == 0

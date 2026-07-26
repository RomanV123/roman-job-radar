import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from src.database.models import Application, Base, Company, Job, JobMatch, PipelineRun


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    s = SessionLocal()
    yield s
    s.close()


def _make_company(session):
    company = Company(name="Genentech", industry="biotech", ats_type="greenhouse", board_identifier="genentech")
    session.add(company)
    session.commit()
    return company


def test_create_company_and_job(session):
    company = _make_company(session)
    job = Job(
        external_id="abc123",
        source="greenhouse",
        company_id=company.id,
        title="OT Cybersecurity Analyst",
        normalized_title="OT Cybersecurity Analyst",
        location="South San Francisco, CA",
        state="CA",
        workplace_type="onsite",
        employment_type="full_time",
        apply_url="https://boards.greenhouse.io/genentech/jobs/abc123",
        content_hash="hash1",
    )
    session.add(job)
    session.commit()
    assert job.id is not None
    assert job.company.name == "Genentech"


def test_duplicate_source_external_id_rejected(session):
    company = _make_company(session)
    session.add(Job(external_id="dup1", source="greenhouse", company_id=company.id, title="A"))
    session.commit()
    session.add(Job(external_id="dup1", source="greenhouse", company_id=company.id, title="B"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_job_match_and_application_relationships(session):
    company = _make_company(session)
    job = Job(external_id="j1", source="lever", company_id=company.id, title="SOC Analyst")
    session.add(job)
    session.commit()

    match = JobMatch(job_id=job.id, total_score=92.5, matching_skills='["Python", "Splunk"]', missing_skills="[]")
    application = Application(job_id=job.id, status="saved")
    session.add_all([match, application])
    session.commit()

    assert job.matches[0].total_score == 92.5
    assert job.applications[0].status == "saved"


def test_application_unique_per_job(session):
    company = _make_company(session)
    job = Job(external_id="j2", source="ashby", company_id=company.id, title="Security Analyst")
    session.add(job)
    session.commit()

    session.add(Application(job_id=job.id, status="saved"))
    session.commit()
    session.add(Application(job_id=job.id, status="applied"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_pipeline_run_defaults(session):
    run = PipelineRun(source="greenhouse")
    session.add(run)
    session.commit()
    assert run.jobs_found == 0
    assert run.new_jobs == 0
    assert run.completed_at is None

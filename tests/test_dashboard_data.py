from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database.models import Application, Base, Company, Job, JobMatch
from src.services.dashboard_data import (
    JobFilters,
    JobRow,
    apply_filters,
    get_or_create_application,
    load_visible_jobs,
    page_application_tracker,
    page_biotech_ot,
    page_california,
    page_full_time,
    page_internships,
    page_nationwide,
    page_recommended,
    page_remote,
    page_saved,
    remove_application,
    set_application_notes,
    set_application_status,
)

REFERENCE_TIME = datetime(2026, 7, 25, tzinfo=timezone.utc)


def make_row(**overrides) -> JobRow:
    defaults = dict(
        job_id=1,
        company_name="Acme",
        industry="cybersecurity",
        title="Security Analyst",
        normalized_title="Security Analyst",
        description="desc",
        location="Sacramento, CA",
        state="CA",
        workplace_type="onsite",
        employment_type="full_time",
        salary_min=90000.0,
        salary_max=110000.0,
        experience_min=1.0,
        experience_max=3.0,
        posted_at=REFERENCE_TIME - timedelta(days=2),
        apply_url="https://example.com/1",
        source_url="https://example.com/1",
        source="greenhouse",
        is_active=True,
        total_score=85.0,
        skills_score=90.0,
        experience_score=90.0,
        title_score=90.0,
        education_score=90.0,
        location_score=100.0,
        semantic_score=60.0,
        freshness_score=95.0,
        matching_skills=["Splunk", "SIEM"],
        missing_required_skills=["AWS"],
        missing_preferred_skills=["Terraform"],
        match_explanation="Great fit",
        application_status=None,
        application_notes=None,
        application_id=None,
    )
    defaults.update(overrides)
    return JobRow(**defaults)


# ---------- apply_filters ----------

def test_filter_min_score():
    rows = [make_row(total_score=90), make_row(job_id=2, total_score=40)]
    result = apply_filters(rows, JobFilters(min_score=60))
    assert len(result) == 1
    assert result[0].total_score == 90


def test_filter_posted_within_days():
    recent = make_row(posted_at=REFERENCE_TIME - timedelta(days=1))
    old = make_row(job_id=2, posted_at=REFERENCE_TIME - timedelta(days=40))
    result = apply_filters([recent, old], JobFilters(posted_within_days=30, reference_time=REFERENCE_TIME))
    assert len(result) == 1


def test_filter_posted_within_days_excludes_unknown_date():
    unknown = make_row(posted_at=None)
    result = apply_filters([unknown], JobFilters(posted_within_days=30, reference_time=REFERENCE_TIME))
    assert result == []


def test_filter_employment_type():
    ft = make_row(employment_type="full_time")
    intern = make_row(job_id=2, employment_type="internship")
    result = apply_filters([ft, intern], JobFilters(employment_types=("internship",)))
    assert len(result) == 1
    assert result[0].employment_type == "internship"


def test_filter_workplace_type():
    remote = make_row(workplace_type="remote")
    onsite = make_row(job_id=2, workplace_type="onsite")
    result = apply_filters([remote, onsite], JobFilters(workplace_types=("remote",)))
    assert len(result) == 1


def test_filter_california_only():
    ca = make_row(state="CA")
    tx = make_row(job_id=2, state="TX")
    result = apply_filters([ca, tx], JobFilters(california_only=True))
    assert len(result) == 1
    assert result[0].state == "CA"


def test_filter_remote_only():
    remote = make_row(workplace_type="remote")
    onsite = make_row(job_id=2, workplace_type="onsite")
    result = apply_filters([remote, onsite], JobFilters(remote_only=True))
    assert len(result) == 1


def test_filter_industries():
    biotech = make_row(industry="biotech")
    cyber = make_row(job_id=2, industry="cybersecurity")
    result = apply_filters([biotech, cyber], JobFilters(industries=("biotech",)))
    assert len(result) == 1


def test_filter_companies():
    a = make_row(company_name="Acme")
    b = make_row(job_id=2, company_name="Other Co")
    result = apply_filters([a, b], JobFilters(companies=("Acme",)))
    assert len(result) == 1


def test_filter_role_category_titles():
    match = make_row(normalized_title="Security Operations Analyst")
    other = make_row(job_id=2, normalized_title="Product Manager")
    result = apply_filters([match, other], JobFilters(role_category_titles=("Security Operations Analyst",)))
    assert len(result) == 1


def test_filter_max_required_experience():
    low = make_row(experience_min=1.0)
    high = make_row(job_id=2, experience_min=5.0)
    result = apply_filters([low, high], JobFilters(max_required_experience=3.0))
    assert len(result) == 1
    assert result[0].experience_min == 1.0


def test_filter_max_required_experience_allows_unknown():
    unknown = make_row(experience_min=None)
    result = apply_filters([unknown], JobFilters(max_required_experience=3.0))
    assert len(result) == 1


def test_filter_min_salary():
    high_pay = make_row(salary_max=150000.0)
    low_pay = make_row(job_id=2, salary_max=60000.0)
    result = apply_filters([high_pay, low_pay], JobFilters(min_salary=100000.0))
    assert len(result) == 1


def test_filter_min_salary_allows_unknown_salary():
    unknown = make_row(salary_max=None)
    result = apply_filters([unknown], JobFilters(min_salary=100000.0))
    assert len(result) == 1


def test_filter_missing_skill():
    missing_aws = make_row(missing_required_skills=["AWS"], missing_preferred_skills=[])
    missing_docker = make_row(job_id=2, missing_required_skills=[], missing_preferred_skills=["Docker"])
    result = apply_filters([missing_aws, missing_docker], JobFilters(missing_skill="AWS"))
    assert len(result) == 1
    assert result[0].job_id == missing_aws.job_id


def test_filters_combine_with_and_logic():
    good = make_row(state="CA", workplace_type="onsite", total_score=90)
    wrong_state = make_row(job_id=2, state="TX", workplace_type="onsite", total_score=90)
    result = apply_filters([good, wrong_state], JobFilters(min_score=60, california_only=True))
    assert len(result) == 1
    assert result[0].job_id == good.job_id


# ---------- page predicates ----------

def test_page_california():
    ca = make_row(state="CA")
    tx = make_row(job_id=2, state="TX")
    assert page_california([ca, tx]) == [ca]


def test_page_nationwide_includes_everything():
    ca = make_row(state="CA")
    tx = make_row(job_id=2, state="TX")
    assert page_nationwide([ca, tx]) == [ca, tx]


def test_page_remote():
    remote = make_row(workplace_type="remote")
    onsite = make_row(job_id=2, workplace_type="onsite")
    assert page_remote([remote, onsite]) == [remote]


def test_page_internships():
    intern = make_row(employment_type="internship")
    full = make_row(job_id=2, employment_type="full_time")
    assert page_internships([intern, full]) == [intern]


def test_page_full_time():
    intern = make_row(employment_type="internship")
    full = make_row(job_id=2, employment_type="full_time")
    assert page_full_time([intern, full]) == [full]


def test_page_biotech_ot():
    biotech = make_row(industry="biotech")
    ot = make_row(job_id=2, industry="ot")
    cyber = make_row(job_id=3, industry="cybersecurity")
    assert set(r.job_id for r in page_biotech_ot([biotech, ot, cyber])) == {1, 2}


def test_page_recommended_sorted_and_thresholded():
    high = make_row(job_id=1, total_score=95)
    mid = make_row(job_id=2, total_score=82)
    low = make_row(job_id=3, total_score=50)
    result = page_recommended([mid, high, low], min_score=80)
    assert [r.job_id for r in result] == [1, 2]


def test_page_saved():
    saved = make_row(application_status="saved")
    applied = make_row(job_id=2, application_status="applied")
    none_status = make_row(job_id=3, application_status=None)
    assert page_saved([saved, applied, none_status]) == [saved]


def test_page_application_tracker_includes_all_statuses():
    saved = make_row(application_status="saved")
    applied = make_row(job_id=2, application_status="applied")
    none_status = make_row(job_id=3, application_status=None)
    result = page_application_tracker([saved, applied, none_status])
    assert set(r.job_id for r in result) == {1, 2}


# ---------- DB-backed: application mutations + load_visible_jobs ----------

@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture
def seeded_job(session):
    company = Company(name="Acme", industry="cybersecurity", ats_type="greenhouse", board_identifier="acme")
    session.add(company)
    session.commit()
    job = Job(
        external_id="1", source="greenhouse", company_id=company.id,
        title="Security Analyst", normalized_title="Security Analyst",
        state="CA", workplace_type="onsite", employment_type="full_time",
        apply_url="https://acme.com/1",
    )
    session.add(job)
    session.commit()
    match = JobMatch(
        job_id=job.id, total_score=85.0, skills_score=90, experience_score=90,
        title_score=90, education_score=90, location_score=100, semantic_score=60, freshness_score=95,
        matching_skills='["Splunk", "SIEM"]', missing_skills='{"required": ["AWS"], "preferred": []}',
        match_explanation="Great fit",
    )
    session.add(match)
    session.commit()
    return job


def test_load_visible_jobs_tolerates_malformed_json_columns(session):
    """Regression-style resilience test: if matching_skills/missing_skills
    ever end up as malformed JSON (e.g. a bug elsewhere, manual DB edit),
    the dashboard must degrade to empty lists instead of crashing the
    whole page for every job."""
    company = Company(name="Acme", ats_type="greenhouse", board_identifier="acme")
    session.add(company)
    session.commit()
    job = Job(external_id="1", source="greenhouse", company_id=company.id, title="Analyst", location="Sacramento, CA")
    session.add(job)
    session.commit()
    match = JobMatch(
        job_id=job.id, total_score=70.0,
        matching_skills="not valid json {{{",
        missing_skills="also not valid json",
    )
    session.add(match)
    session.commit()

    rows = load_visible_jobs(session)
    assert len(rows) == 1
    assert rows[0].matching_skills == []
    assert rows[0].missing_required_skills == []
    assert rows[0].missing_preferred_skills == []


def test_load_visible_jobs_tolerates_flat_list_missing_skills_shape(session):
    company = Company(name="Acme", ats_type="greenhouse", board_identifier="acme")
    session.add(company)
    session.commit()
    job = Job(external_id="1", source="greenhouse", company_id=company.id, title="Analyst", location="Sacramento, CA")
    session.add(job)
    session.commit()
    match = JobMatch(job_id=job.id, total_score=70.0, missing_skills='["AWS", "Docker"]')
    session.add(match)
    session.commit()

    rows = load_visible_jobs(session)
    assert rows[0].missing_required_skills == ["AWS", "Docker"]
    assert rows[0].missing_preferred_skills == []


def _add_job_with_match(session, company, external_id, location=None, state=None, total_score=70.0):
    job = Job(
        external_id=external_id, source="greenhouse", company_id=company.id,
        title="Analyst", location=location, state=state,
    )
    session.add(job)
    session.commit()
    session.add(JobMatch(job_id=job.id, total_score=total_score))
    session.commit()
    return job


def test_load_visible_jobs_excludes_confirmed_non_us(session):
    company = Company(name="Acme", ats_type="greenhouse", board_identifier="acme")
    session.add(company)
    session.commit()
    _add_job_with_match(session, company, "us-job", location="Sacramento, CA")
    _add_job_with_match(session, company, "uk-job", location="London, United Kingdom")

    rows = load_visible_jobs(session)

    assert [r.job_id for r in rows] == [1]  # only the US job survives


def test_load_visible_jobs_excludes_ambiguous_location():
    """A bare "Remote" with no country/state signal is unknown, not
    confirmed-US -- the US-only filter is deliberately strict and excludes
    it too, not just jobs confirmed to be outside the US."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    company = Company(name="Acme", ats_type="greenhouse", board_identifier="acme")
    session.add(company)
    session.commit()
    _add_job_with_match(session, company, "ambiguous-job", location="Remote")

    rows = load_visible_jobs(session)

    assert rows == []


def test_load_visible_jobs_includes_job_identified_by_state_alone():
    """A job with no free-text location but a resolved `state` column
    (already-parsed structured data) should still count as confirmed US."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    company = Company(name="Acme", ats_type="greenhouse", board_identifier="acme")
    session.add(company)
    session.commit()
    _add_job_with_match(session, company, "state-only-job", location=None, state="CA")

    rows = load_visible_jobs(session)

    assert len(rows) == 1


def test_load_visible_jobs_returns_expected_row(session, seeded_job):
    rows = load_visible_jobs(session)
    assert len(rows) == 1
    row = rows[0]
    assert row.company_name == "Acme"
    assert row.matching_skills == ["Splunk", "SIEM"]
    assert row.missing_required_skills == ["AWS"]
    assert row.application_status is None


def test_load_visible_jobs_uses_latest_match(session, seeded_job):
    older = JobMatch(
        job_id=seeded_job.id, total_score=50.0,
        evaluated_at=datetime.now(timezone.utc) - timedelta(days=5),
    )
    session.add(older)
    session.commit()
    rows = load_visible_jobs(session)
    assert len(rows) == 1  # not duplicated per match row
    assert rows[0].total_score == 85.0  # the newer match wins


def test_load_visible_jobs_excludes_inactive_by_default(session, seeded_job):
    seeded_job.is_active = False
    session.commit()
    rows = load_visible_jobs(session)
    assert rows == []


def test_get_or_create_application_creates_once(session, seeded_job):
    app1 = get_or_create_application(session, seeded_job.id)
    app2 = get_or_create_application(session, seeded_job.id)
    session.commit()
    assert app1.id == app2.id
    assert app1.status == "saved"


def test_set_application_status_sets_date_applied(session, seeded_job):
    application = set_application_status(session, seeded_job.id, "applied")
    session.commit()
    assert application.status == "applied"
    assert application.date_applied is not None


def test_set_application_notes(session, seeded_job):
    application = set_application_notes(session, seeded_job.id, "Great culture, following up next week")
    session.commit()
    assert application.notes == "Great culture, following up next week"


def test_remove_application(session, seeded_job):
    set_application_status(session, seeded_job.id, "saved")
    session.commit()
    remove_application(session, seeded_job.id)
    session.commit()
    rows = load_visible_jobs(session)
    assert rows[0].application_status is None

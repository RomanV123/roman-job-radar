from datetime import datetime, timezone
from types import SimpleNamespace

from src.services.dashboard_data import JobRow
from src.services.job_board_export import (
    BoardJob,
    _bucket_all_jobs,
    _bucket_matched_jobs,
    _escape,
    _fmt_date,
    _render_table,
    _sort_key_posted_at,
)


def make_job_row(**overrides) -> JobRow:
    defaults = dict(
        job_id=1,
        company_name="Acme",
        industry="cybersecurity",
        title="SOC Analyst",
        normalized_title="SOC Analyst",
        description=None,
        location="Sacramento, CA",
        state="CA",
        workplace_type="remote",
        employment_type="full_time",
        salary_min=None,
        salary_max=None,
        experience_min=None,
        experience_max=None,
        posted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        apply_url="https://acme.com/jobs/1",
        source_url="https://acme.com/jobs/1",
        source="greenhouse",
        is_active=True,
        total_score=85.0,
        skills_score=0.8,
        experience_score=0.8,
        title_score=0.9,
        education_score=1.0,
        location_score=1.0,
        semantic_score=0.7,
        freshness_score=1.0,
        matching_skills=["SIEM"],
        missing_required_skills=[],
        missing_preferred_skills=[],
        match_explanation="Strong match",
        application_status=None,
        application_notes=None,
        application_id=None,
    )
    defaults.update(overrides)
    return JobRow(**defaults)


def make_job_company(title="SOC Analyst", posted_at=None, company_name="Acme", employment_type="full_time"):
    job = SimpleNamespace(
        title=title,
        location="Sacramento, CA",
        workplace_type="remote",
        employment_type=employment_type,
        posted_at=posted_at or datetime(2026, 1, 1, tzinfo=timezone.utc),
        apply_url="https://acme.com/jobs/1",
        source_url="https://acme.com/jobs/1",
    )
    company = SimpleNamespace(name=company_name)
    return job, company


def test_escape_handles_pipes_and_newlines():
    assert _escape("Sales | Marketing") == "Sales \\| Marketing"
    assert _escape("Line one\nLine two") == "Line one Line two"
    assert _escape(None) == ""


def test_fmt_date_handles_none():
    assert _fmt_date(None) == ""
    assert _fmt_date(datetime(2026, 3, 5, tzinfo=timezone.utc)) == "2026-03-05"


def test_sort_key_posted_at_normalizes_naive_datetime():
    naive = datetime(2026, 1, 1)
    aware = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert _sort_key_posted_at(naive) == aware
    assert _sort_key_posted_at(None).tzinfo is not None


def test_bucket_all_jobs_groups_by_category_and_sorts_by_recency():
    old_job = make_job_company(title="SOC Analyst", posted_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    new_job = make_job_company(title="Security Engineer", posted_at=datetime(2026, 6, 1, tzinfo=timezone.utc))
    unrelated = make_job_company(title="Warehouse Associate")

    buckets = _bucket_all_jobs([old_job, new_job, unrelated])

    cyber_full_time = buckets["cybersecurity"]["full_time"]
    assert len(cyber_full_time) == 2
    assert cyber_full_time[0].title == "Security Engineer"  # most recent first
    assert buckets["software_engineering"]["full_time"] == []  # "Warehouse Associate" matches no category


def test_bucket_all_jobs_splits_by_employment_type():
    full_time = make_job_company(title="SOC Analyst", employment_type="full_time")
    intern = make_job_company(title="Security Intern", employment_type="internship")
    contract = make_job_company(title="Security Contractor", employment_type="contract")

    buckets = _bucket_all_jobs([full_time, intern, contract])

    assert len(buckets["cybersecurity"]["full_time"]) == 1
    assert len(buckets["cybersecurity"]["internship"]) == 1
    assert buckets["cybersecurity"]["full_time"][0].title == "SOC Analyst"
    assert buckets["cybersecurity"]["internship"][0].title == "Security Intern"
    # contract isn't one of the two board employment types -- excluded entirely
    all_titles = [j.title for et in buckets["cybersecurity"].values() for j in et]
    assert "Security Contractor" not in all_titles


def test_bucket_matched_jobs_filters_below_min_score():
    high = make_job_row(title="SOC Analyst", total_score=85.0)
    low = make_job_row(title="Security Analyst", total_score=40.0)

    buckets = _bucket_matched_jobs([high, low])

    assert len(buckets["cybersecurity"]["full_time"]) == 1
    assert buckets["cybersecurity"]["full_time"][0].score == 85.0


def test_bucket_matched_jobs_sorts_by_score_descending():
    lower = make_job_row(title="Security Analyst", total_score=65.0)
    higher = make_job_row(title="SOC Analyst", total_score=90.0)

    buckets = _bucket_matched_jobs([lower, higher])

    scores = [j.score for j in buckets["cybersecurity"]["full_time"]]
    assert scores == sorted(scores, reverse=True)


def test_bucket_matched_jobs_splits_by_employment_type():
    full_time = make_job_row(title="SOC Analyst", employment_type="full_time", total_score=80.0)
    intern = make_job_row(title="Security Intern", employment_type="internship", total_score=75.0)

    buckets = _bucket_matched_jobs([full_time, intern])

    assert len(buckets["cybersecurity"]["full_time"]) == 1
    assert len(buckets["cybersecurity"]["internship"]) == 1


def test_render_table_empty_list():
    assert "No jobs" in _render_table([], show_score=False)


def test_render_table_includes_score_column_when_requested():
    job = BoardJob(
        title="SOC Analyst",
        company="Acme",
        location="Sacramento, CA",
        workplace_type="remote",
        employment_type="full_time",
        posted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        apply_url="https://acme.com/jobs/1",
        score=85.0,
    )
    table = _render_table([job], show_score=True)
    assert "Score" in table
    assert "85" in table
    assert "[Apply](https://acme.com/jobs/1)" in table

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.processing.eligibility import (
    check_clearance_and_citizenship,
    check_country,
    check_employment_type,
    check_expired,
    check_internship_excludes_graduates,
    check_lookback,
    check_required_experience,
    check_seniority,
    evaluate_eligibility,
    extract_required_experience_min,
    split_required_and_preferred,
)
from src.processing.normalize import NormalizedJob

REFERENCE_TIME = datetime(2026, 7, 25, tzinfo=timezone.utc)


def make_normalized_job(**overrides) -> NormalizedJob:
    defaults = dict(
        external_id="1",
        source="greenhouse",
        title="Security Analyst",
        normalized_title="Security Analyst",
        description="Analyze security incidents. 1-3 years of experience preferred.",
        location="Sacramento, CA",
        state="CA",
        is_us=True,
        workplace_type="onsite",
        employment_type="full_time",
        salary_min=None,
        salary_max=None,
        experience_min=1.0,
        experience_max=3.0,
        posted_at=REFERENCE_TIME - timedelta(days=5),
        apply_url="https://example.com/jobs/1",
        source_url="https://example.com/jobs/1",
        content_hash="hash",
    )
    defaults.update(overrides)
    return NormalizedJob(**defaults)


def make_profile(**eligibility_overrides):
    eligibility = {
        "graduate_student_status": True,
        "citizenship_restricted_roles_eligible": None,
        "max_years_experience_have": 2,
    }
    eligibility.update(eligibility_overrides)
    return SimpleNamespace(eligibility=eligibility)


# ---------- split_required_and_preferred ----------

def test_split_with_preferred_header():
    text = "Required Qualifications: 3 years Python. Preferred Qualifications: AWS experience."
    required, preferred = split_required_and_preferred(text)
    assert "3 years Python" in required
    assert "AWS experience" in preferred
    assert "AWS experience" not in required


def test_split_without_preferred_header_returns_whole_text_as_required():
    text = "You will work on security tooling and Python automation."
    required, preferred = split_required_and_preferred(text)
    assert required == text
    assert preferred == ""


# ---------- extract_required_experience_min ----------

def test_required_experience_extracted_from_required_section():
    text = "Requirements: 3+ years of experience. Preferred Qualifications: 5+ years leading teams."
    assert extract_required_experience_min(text) == 3.0


def test_required_experience_ignores_inline_preferred_sentence():
    text = "You will do security work. 1-3 years of experience preferred for this role."
    assert extract_required_experience_min(text) is None


def test_required_experience_none_when_missing():
    assert extract_required_experience_min("No experience requirement mentioned.") is None
    assert extract_required_experience_min(None) is None


# ---------- check_seniority ----------

def test_seniority_rejects_sr_abbreviation():
    """Regression test: a real BigID posting titled "Sr. Services Delivery
    Engineer" slipped past the filter because it only matched the spelled-
    out word "Senior", not the common "Sr." abbreviation."""
    assert check_seniority("Sr. Services Delivery Engineer") is not None
    assert check_seniority("Sr Director of Sales") is not None


def test_seniority_mgr_dir_abbreviations():
    assert check_seniority("Regional Mgr.") is not None
    assert check_seniority("IT Dir.") is not None



def test_seniority_rejects_senior_title():
    assert check_seniority("Senior Security Engineer") is not None


def test_seniority_allows_analyst_i():
    assert check_seniority("Security Engineer I") is None


def test_seniority_word_boundary_does_not_false_positive():
    # "Leadership" contains "Lead" as substring but shouldn't match word-boundary regex
    assert check_seniority("Leadership Development Program Analyst") is None


def test_seniority_manually_enabled_overrides():
    assert check_seniority("Senior Security Engineer", manually_enabled={"senior"}) is None


# ---------- check_required_experience ----------

def test_required_experience_rejects_above_max():
    assert check_required_experience("Requirements: 5+ years required.", max_years=3) is not None


def test_required_experience_allows_at_or_below_max():
    assert check_required_experience("Requirements: 2 years required.", max_years=3) is None


def test_required_experience_allows_preferred_only_mention():
    assert check_required_experience("1-3 years of experience preferred.", max_years=3) is None


# ---------- check_country ----------

def test_country_rejects_non_us():
    assert check_country(False) is not None


def test_country_allows_us_and_unknown():
    assert check_country(True) is None
    assert check_country(None) is None


# ---------- check_employment_type ----------

def test_employment_type_rejects_contract_by_default():
    assert check_employment_type("contract") is not None


def test_employment_type_allows_contract_when_manually_enabled():
    assert check_employment_type("contract", manually_enabled={"contract"}) is None


def test_employment_type_allows_full_time_and_internship():
    assert check_employment_type("full_time") is None
    assert check_employment_type("internship") is None


def test_employment_type_allows_unknown():
    assert check_employment_type(None) is None


# ---------- check_internship_excludes_graduates ----------

def test_internship_excludes_graduates_rejects_when_stated():
    result = check_internship_excludes_graduates(
        "internship", "This program is for undergraduate students only.", is_graduate_student=True
    )
    assert result is not None


def test_internship_does_not_reject_non_graduate():
    result = check_internship_excludes_graduates(
        "internship", "This program is for undergraduate students only.", is_graduate_student=False
    )
    assert result is None


def test_internship_does_not_reject_when_not_stated():
    result = check_internship_excludes_graduates("internship", "Great internship opportunity.", is_graduate_student=True)
    assert result is None


def test_internship_check_skipped_for_full_time():
    result = check_internship_excludes_graduates(
        "full_time", "This program is for undergraduate students only.", is_graduate_student=True
    )
    assert result is None


# ---------- check_clearance_and_citizenship ----------

def test_clearance_rejects_when_profile_marks_ineligible():
    reason, warning = check_clearance_and_citizenship("Must be a U.S. citizen.", citizenship_restricted_roles_eligible=False)
    assert reason is not None
    assert warning is None


def test_clearance_warns_when_unknown():
    reason, warning = check_clearance_and_citizenship("Requires an active security clearance.", citizenship_restricted_roles_eligible=None)
    assert reason is None
    assert warning is not None


def test_clearance_warns_even_when_eligible_true():
    reason, warning = check_clearance_and_citizenship("Must be a U.S. citizen.", citizenship_restricted_roles_eligible=True)
    assert reason is None
    assert warning is not None


def test_clearance_no_mention_no_reason_no_warning():
    reason, warning = check_clearance_and_citizenship("Great team culture.", citizenship_restricted_roles_eligible=False)
    assert reason is None
    assert warning is None


# ---------- check_expired / check_lookback ----------

def test_expired_rejects_inactive():
    assert check_expired(False) is not None
    assert check_expired(True) is None


def test_lookback_rejects_old_posting():
    posted = REFERENCE_TIME - timedelta(days=45)
    assert check_lookback(posted, lookback_days=30, reference_time=REFERENCE_TIME) is not None


def test_lookback_allows_recent_posting():
    posted = REFERENCE_TIME - timedelta(days=5)
    assert check_lookback(posted, lookback_days=30, reference_time=REFERENCE_TIME) is None


def test_lookback_handles_naive_datetime_as_utc():
    """posted_at can arrive without tzinfo (e.g. a source that doesn't
    specify a timezone) — must not raise a naive/aware comparison TypeError."""
    naive_posted = datetime(2026, 6, 1)  # no tzinfo
    result = check_lookback(naive_posted, lookback_days=30, reference_time=REFERENCE_TIME)
    assert result is not None  # June 1 is well outside a 30-day window from July 25


def test_lookback_allows_unknown_posted_date():
    assert check_lookback(None, lookback_days=30, reference_time=REFERENCE_TIME) is None


# ---------- evaluate_eligibility (integration) ----------

def test_evaluate_eligibility_fully_eligible_job():
    job = make_normalized_job()
    profile = make_profile()
    result = evaluate_eligibility(job, profile, reference_time=REFERENCE_TIME)
    assert result.eligible is True
    assert result.reasons == []


def test_evaluate_eligibility_rejects_senior_title():
    job = make_normalized_job(title="Senior Security Analyst")
    profile = make_profile()
    result = evaluate_eligibility(job, profile, reference_time=REFERENCE_TIME)
    assert result.eligible is False
    assert any("seniority" in r.lower() for r in result.reasons)


def test_evaluate_eligibility_accumulates_multiple_reasons():
    job = make_normalized_job(
        title="Senior Security Manager",
        is_us=False,
        posted_at=REFERENCE_TIME - timedelta(days=90),
    )
    profile = make_profile()
    result = evaluate_eligibility(job, profile, lookback_days=30, reference_time=REFERENCE_TIME)
    assert result.eligible is False
    assert len(result.reasons) >= 3


def test_evaluate_eligibility_does_not_reject_remote_us_outside_california():
    job = make_normalized_job(location="Remote - US", state=None, is_us=True, workplace_type="remote")
    profile = make_profile()
    result = evaluate_eligibility(job, profile, reference_time=REFERENCE_TIME)
    assert result.eligible is True


def test_evaluate_eligibility_does_not_reject_non_california_us_job():
    job = make_normalized_job(location="Austin, TX", state="TX")
    profile = make_profile()
    result = evaluate_eligibility(job, profile, reference_time=REFERENCE_TIME)
    assert result.eligible is True


def test_evaluate_eligibility_surfaces_clearance_warning_without_rejecting():
    job = make_normalized_job(description="Must be a U.S. citizen. Great opportunity.")
    profile = make_profile(citizenship_restricted_roles_eligible=None)
    result = evaluate_eligibility(job, profile, reference_time=REFERENCE_TIME)
    assert result.eligible is True
    assert len(result.warnings) == 1


def test_evaluate_eligibility_rejects_when_profile_marks_citizenship_ineligible():
    job = make_normalized_job(description="Must be a U.S. citizen for this role.")
    profile = make_profile(citizenship_restricted_roles_eligible=False)
    result = evaluate_eligibility(job, profile, reference_time=REFERENCE_TIME)
    assert result.eligible is False
    assert any("citizenship" in r.lower() or "clearance" in r.lower() for r in result.reasons)


def test_evaluate_eligibility_expired_job_rejected():
    job = make_normalized_job()
    profile = make_profile()
    result = evaluate_eligibility(job, profile, is_active=False, reference_time=REFERENCE_TIME)
    assert result.eligible is False

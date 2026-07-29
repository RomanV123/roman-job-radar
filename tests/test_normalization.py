from datetime import datetime, timezone

from src.collectors.base import RawJob
from src.processing.normalize import (
    clean_html,
    compute_content_hash,
    extract_employment_type,
    extract_experience_years,
    extract_location,
    extract_salary,
    extract_workplace_type,
    load_title_aliases,
    normalize_job,
    normalize_title,
    parse_posted_date,
)

REFERENCE_TIME = datetime(2026, 7, 25, tzinfo=timezone.utc)


def raw_job(**overrides) -> RawJob:
    defaults = dict(
        external_id="ext-1",
        title="Security Analyst",
        location_text="Sacramento, CA",
        description_html="<p>3-5 years of experience required. $100,000 - $130,000</p>",
        apply_url="https://example.com/apply/1",
        source_url="https://example.com/jobs/1",
        posted_at=None,
        department=None,
        raw={},
    )
    defaults.update(overrides)
    return RawJob(**defaults)


# ---------- Title ----------

def test_normalize_title_uses_alias():
    aliases = load_title_aliases()
    assert normalize_title("InfoSec Analyst II", aliases) == "Information Security Analyst"
    assert normalize_title("Cyber Defense Analyst", aliases) == "Cybersecurity Analyst"


def test_normalize_title_passthrough_when_no_alias_matches():
    aliases = load_title_aliases()
    assert normalize_title("Staff Platform Engineer", aliases) == "Staff Platform Engineer"


# ---------- HTML cleaning ----------

def test_clean_html_strips_tags_and_collapses_whitespace():
    html = "<p>Hello   <b>world</b></p>\n<p>Second   line</p>"
    assert clean_html(html) == "Hello world Second line"


def test_clean_html_none_and_empty():
    assert clean_html(None) is None
    assert clean_html("") is None
    assert clean_html("<p></p>") is None


# ---------- Location ----------

def test_location_lever_us():
    job = raw_job(location_text="Sacramento, CA", raw={"country": "US"})
    info = extract_location(job, "lever")
    assert info.is_us is True
    assert info.state == "CA"


def test_location_lever_non_us():
    job = raw_job(location_text="London, United Kingdom", raw={"country": "GB"})
    info = extract_location(job, "lever")
    assert info.is_us is False
    assert info.state is None


def test_location_ashby_us_with_region():
    job = raw_job(
        location_text="South San Francisco, CA",
        raw={"address": {"postalAddress": {"addressCountry": "United States", "addressRegion": "California"}}},
    )
    info = extract_location(job, "ashby")
    assert info.is_us is True
    assert info.state == "CA"


def test_location_ashby_us_iso_country_code():
    """Regression test: some Ashby-hosted boards send addressCountry as the
    ISO code ("US") instead of the full name ("United States") — caught via
    a live 1Password job where this was being misclassified as non-US."""
    job = raw_job(location_text="United States", raw={"address": {"postalAddress": {"addressCountry": "US"}}})
    info = extract_location(job, "ashby")
    assert info.is_us is True


def test_location_ashby_non_us():
    job = raw_job(
        location_text="Dublin",
        raw={"address": {"postalAddress": {"addressCountry": "Ireland"}}},
    )
    info = extract_location(job, "ashby")
    assert info.is_us is False
    assert info.state is None


def test_location_greenhouse_uses_office_location_for_country():
    job = raw_job(
        location_text="Sydney",
        raw={"offices": [{"location": "Sydney, New South Wales, Australia"}]},
    )
    info = extract_location(job, "greenhouse")
    assert info.is_us is False


def test_location_greenhouse_plain_us_location():
    job = raw_job(location_text="Pleasanton, California, USA HQ", raw={"offices": []})
    info = extract_location(job, "greenhouse")
    assert info.is_us is True
    assert info.state == "CA"


def test_location_government_structured():
    job = raw_job(
        location_text="Sacramento, CA",
        raw={
            "MatchedObjectDescriptor": {
                "PositionLocation": [{"CountryCode": "US", "CountrySubDivisionCode": "CA"}]
            }
        },
    )
    info = extract_location(job, "government")
    assert info.is_us is True
    assert info.state == "CA"


def test_location_workday_text_fallback_us():
    job = raw_job(location_text="US - California - Thousand Oaks", raw={})
    info = extract_location(job, "workday")
    assert info.is_us is True
    assert info.state == "CA"


def test_location_workday_text_fallback_non_us():
    job = raw_job(location_text="Mexico-Guadalajara", raw={})
    info = extract_location(job, "workday")
    assert info.is_us is False


def test_location_greenhouse_bulgaria_detected_as_non_us():
    """Regression test: a real BigID/Greenhouse posting located in
    "Sofia Capital, Bulgaria" was misclassified as is_us=None (unknown,
    not rejected) because Bulgaria was missing from a short, ad hoc
    country list. Greenhouse gives no structured country code here — this
    has to work from text alone."""
    job = raw_job(
        location_text="Bulgaria",
        raw={"offices": [{"location": "Sofia Capital, Bulgaria"}]},
    )
    info = extract_location(job, "greenhouse")
    assert info.is_us is False


def test_location_ambiguous_placeholder_is_unknown():
    job = raw_job(location_text="2 Locations", raw={})
    info = extract_location(job, "workday")
    assert info.is_us is None
    assert info.state is None


def test_location_missing_text_is_unknown():
    job = raw_job(location_text=None, raw={})
    info = extract_location(job, "custom")
    assert info.is_us is None
    assert info.display is None


# ---------- Workplace type ----------

def test_workplace_type_lever_structured():
    job = raw_job(raw={"workplaceType": "hybrid"})
    assert extract_workplace_type(job, "lever") == "hybrid"


def test_workplace_type_ashby_structured():
    job = raw_job(raw={"workplaceType": "Remote"})
    assert extract_workplace_type(job, "ashby") == "remote"


def test_workplace_type_ashby_is_remote_flag():
    job = raw_job(raw={"isRemote": True})
    assert extract_workplace_type(job, "ashby") == "remote"


def test_workplace_type_greenhouse_metadata():
    job = raw_job(raw={"metadata": [{"name": "Location Type", "value": "On-Site"}]})
    assert extract_workplace_type(job, "greenhouse") == "onsite"


def test_workplace_type_text_fallback_remote():
    job = raw_job(location_text="Remote - US", raw={})
    assert extract_workplace_type(job, "workday") == "remote"


def test_workplace_type_text_fallback_default_onsite():
    job = raw_job(location_text="Sacramento, CA", raw={})
    assert extract_workplace_type(job, "workday") == "onsite"


def test_workplace_type_unknown_when_no_signal():
    job = raw_job(location_text=None, raw={})
    assert extract_workplace_type(job, "custom") is None


# ---------- Employment type ----------

def test_employment_type_lever_internship():
    job = raw_job(raw={"categories": {"commitment": "Internship"}})
    assert extract_employment_type(job, "lever") == "internship"


def test_employment_type_lever_fulltime():
    job = raw_job(raw={"categories": {"commitment": "Full-time"}})
    assert extract_employment_type(job, "lever") == "full_time"


def test_employment_type_ashby_intern():
    job = raw_job(raw={"employmentType": "Intern"})
    assert extract_employment_type(job, "ashby") == "internship"


def test_employment_type_text_fallback_intern_title():
    job = raw_job(title="Cybersecurity Intern", raw={})
    assert extract_employment_type(job, "workday") == "internship"


def test_employment_type_text_fallback_contract():
    job = raw_job(title="Security Analyst", description_html="<p>This is a contractor position.</p>", raw={})
    assert extract_employment_type(job, "custom", "This is a contractor position.") == "contract"


def test_employment_type_defaults_full_time():
    job = raw_job(title="Security Analyst", description_html="<p>Great team.</p>", raw={})
    assert extract_employment_type(job, "custom") == "full_time"


def test_employment_type_intern_title_overrides_mislabeled_ashby_field():
    """Regression test: real Ashby postings have been observed with a
    title clearly saying "Intern" but employmentType incorrectly set to
    "FullTime" by the employer -- the title must win."""
    job = raw_job(title="Software Engineering Intern", raw={"employmentType": "FullTime"})
    assert extract_employment_type(job, "ashby") == "internship"


def test_employment_type_intern_title_overrides_mislabeled_lever_field():
    job = raw_job(title="Forward Deployed Engineer, Internship", raw={"categories": {"commitment": "Full-time"}})
    assert extract_employment_type(job, "lever") == "internship"


# ---------- Salary ----------

def test_salary_ashby_structured_annual():
    job = raw_job(
        raw={
            "compensation": {
                "summaryComponents": [
                    {"compensationType": "Salary", "interval": "1 YEAR", "minValue": 120000, "maxValue": 150000}
                ]
            }
        }
    )
    low, high = extract_salary(job, "ashby")
    assert (low, high) == (120000.0, 150000.0)


def test_salary_text_plain_dollars():
    job = raw_job(description_html="<p>Compensation: $100,000 - $130,000 per year</p>", raw={})
    low, high = extract_salary(job, "greenhouse", "Compensation: $100,000 - $130,000 per year")
    assert (low, high) == (100000.0, 130000.0)


def test_salary_text_k_suffix():
    job = raw_job(raw={})
    low, high = extract_salary(job, "greenhouse", "Pay range $100K - $130K")
    assert (low, high) == (100000.0, 130000.0)


def test_salary_hourly_converted_to_annual():
    job = raw_job(raw={})
    low, high = extract_salary(job, "greenhouse", "$45/hr - $55/hr")
    assert low == 45 * 2080
    assert high == 55 * 2080


def test_salary_no_match_returns_none():
    job = raw_job(raw={})
    assert extract_salary(job, "greenhouse", "No compensation info here.") == (None, None)


# ---------- Experience ----------

def test_experience_range():
    assert extract_experience_years("Looking for 1-3 years of experience") == (1.0, 3.0)


def test_experience_plus():
    assert extract_experience_years("5+ years of relevant experience") == (5.0, None)


def test_experience_none():
    assert extract_experience_years("No experience requirement mentioned") == (None, None)


def test_experience_missing_description():
    assert extract_experience_years(None) == (None, None)


# ---------- Posted date ----------

def test_posted_date_greenhouse_iso():
    dt = parse_posted_date("2026-01-01T00:00:00-04:00", "greenhouse")
    assert dt.year == 2026 and dt.month == 1 and dt.day == 1


def test_posted_date_lever_epoch_millis():
    dt = parse_posted_date("1700000000000", "lever")
    assert dt.year == 2023


def test_posted_date_ashby_iso():
    dt = parse_posted_date("2026-02-01", "ashby")
    assert dt.year == 2026 and dt.month == 2


def test_posted_date_workday_today():
    dt = parse_posted_date("Posted Today", "workday", reference_time=REFERENCE_TIME)
    assert dt == REFERENCE_TIME


def test_posted_date_workday_yesterday():
    dt = parse_posted_date("Posted Yesterday", "workday", reference_time=REFERENCE_TIME)
    assert (REFERENCE_TIME - dt).days == 1


def test_posted_date_workday_n_days_ago():
    dt = parse_posted_date("Posted 3 Days Ago", "workday", reference_time=REFERENCE_TIME)
    assert (REFERENCE_TIME - dt).days == 3


def test_posted_date_workday_plus_days():
    dt = parse_posted_date("Posted 30+ Days Ago", "workday", reference_time=REFERENCE_TIME)
    assert (REFERENCE_TIME - dt).days == 30


def test_posted_date_workday_unrecognized_text_returns_none():
    assert parse_posted_date("Applications close soon", "workday", reference_time=REFERENCE_TIME) is None


def test_posted_date_lever_non_numeric_falls_back_to_generic_parse():
    """If Lever's createdAt somehow isn't a plain epoch-millis string, fall
    through to the generic dateutil parse rather than raising."""
    dt = parse_posted_date("2026-01-01T00:00:00Z", "lever")
    assert dt.year == 2026 and dt.month == 1 and dt.day == 1


def test_posted_date_government_iso():
    dt = parse_posted_date("2026-01-15", "government")
    assert dt.year == 2026 and dt.month == 1 and dt.day == 15


def test_posted_date_invalid_returns_none():
    assert parse_posted_date("not a date at all !!", "custom") is None


def test_posted_date_missing_returns_none():
    assert parse_posted_date(None, "greenhouse") is None


# ---------- Content hash ----------

def test_content_hash_stable_for_same_input():
    h1 = compute_content_hash("Acme", "Security Analyst", "Sacramento, CA", "Some description")
    h2 = compute_content_hash("Acme", "Security Analyst", "Sacramento, CA", "Some description")
    assert h1 == h2


def test_content_hash_differs_for_different_company():
    h1 = compute_content_hash("Acme", "Security Analyst", "Sacramento, CA", "Some description")
    h2 = compute_content_hash("Other Co", "Security Analyst", "Sacramento, CA", "Some description")
    assert h1 != h2


def test_content_hash_case_insensitive():
    h1 = compute_content_hash("Acme", "Security Analyst", "Sacramento, CA", "desc")
    h2 = compute_content_hash("ACME", "SECURITY ANALYST", "sacramento, ca", "DESC")
    assert h1 == h2


# ---------- Full integration ----------

def test_normalize_job_end_to_end_greenhouse():
    aliases = load_title_aliases()
    job = raw_job(
        external_id="123",
        title="SOC Analyst I",
        location_text="Sacramento, CA",
        description_html="<p>1-3 years of experience. $90,000 - $110,000.</p>",
        posted_at="2026-01-01T00:00:00-04:00",
        raw={"offices": []},
    )
    result = normalize_job(job, source="greenhouse", company_name="Acme", title_aliases=aliases)
    assert result.normalized_title == "Security Operations Analyst"
    assert result.state == "CA"
    assert result.is_us is True
    assert result.employment_type == "full_time"
    assert result.experience_min == 1.0 and result.experience_max == 3.0
    assert result.salary_min == 90000.0 and result.salary_max == 110000.0
    assert result.posted_at.year == 2026
    assert result.content_hash and len(result.content_hash) == 64

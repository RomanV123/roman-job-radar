import time

import httpx
import pytest
import respx

from src.collectors.amazon import AmazonCollector
from src.collectors.ashby import AshbyCollector
from src.collectors.base import CollectorError, CompanySource
from src.collectors.custom_page import CustomPageCollector
from src.collectors.government import GovernmentCollector
from src.collectors.greenhouse import GreenhouseCollector
from src.collectors.lever import LeverCollector
from src.collectors.workday import WorkdayCollector


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    """Collector tests never hit the network, but the rate limiter and
    tenacity's retry backoff both call time.sleep — stub it so retry tests
    don't actually wait several seconds."""
    monkeypatch.setattr(time, "sleep", lambda seconds: None)


def make_collector(cls, **kwargs):
    return cls(min_request_interval=0.0, max_attempts=3, **kwargs)


# ---------- Greenhouse ----------

@respx.mock
def test_greenhouse_happy_path():
    company = CompanySource(name="Acme", ats_type="greenhouse", board_identifier="acme")
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").mock(
        return_value=httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": 1,
                        "title": "SOC Analyst",
                        "location": {"name": "Sacramento, CA"},
                        "content": "<p>Do security things</p>",
                        "absolute_url": "https://acme.com/jobs/1",
                        "updated_at": "2026-01-01T00:00:00Z",
                        "departments": [{"name": "Security"}],
                    }
                ]
            },
        )
    )
    collector = make_collector(GreenhouseCollector)
    jobs = collector.fetch_jobs(company)
    assert len(jobs) == 1
    assert jobs[0].external_id == "1"
    assert jobs[0].title == "SOC Analyst"
    assert jobs[0].location_text == "Sacramento, CA"
    assert jobs[0].department == "Security"


@respx.mock
def test_greenhouse_skips_malformed_entries():
    company = CompanySource(name="Acme", ats_type="greenhouse", board_identifier="acme")
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").mock(
        return_value=httpx.Response(200, json={"jobs": [{"no_id_or_title": True}, {"id": 2, "title": "Analyst"}]})
    )
    collector = make_collector(GreenhouseCollector)
    jobs = collector.fetch_jobs(company)
    assert len(jobs) == 1
    assert jobs[0].external_id == "2"


@respx.mock
def test_greenhouse_unexpected_shape_raises():
    company = CompanySource(name="Acme", ats_type="greenhouse", board_identifier="acme")
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").mock(
        return_value=httpx.Response(200, json={"unexpected": "shape"})
    )
    collector = make_collector(GreenhouseCollector)
    with pytest.raises(CollectorError):
        collector.fetch_jobs(company)


def test_greenhouse_missing_board_identifier_raises():
    company = CompanySource(name="Acme", ats_type="greenhouse", board_identifier=None)
    collector = make_collector(GreenhouseCollector)
    with pytest.raises(CollectorError):
        collector.fetch_jobs(company)


@respx.mock
def test_greenhouse_404_raises_collector_error():
    company = CompanySource(name="Acme", ats_type="greenhouse", board_identifier="doesnotexist")
    respx.get("https://boards-api.greenhouse.io/v1/boards/doesnotexist/jobs").mock(
        return_value=httpx.Response(404)
    )
    collector = make_collector(GreenhouseCollector)
    with pytest.raises(CollectorError):
        collector.fetch_jobs(company)


@respx.mock
def test_greenhouse_retries_on_500_then_succeeds():
    company = CompanySource(name="Acme", ats_type="greenhouse", board_identifier="acme")
    route = respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs")
    route.side_effect = [
        httpx.Response(500),
        httpx.Response(500),
        httpx.Response(200, json={"jobs": []}),
    ]
    collector = make_collector(GreenhouseCollector)
    jobs = collector.fetch_jobs(company)
    assert jobs == []
    assert route.call_count == 3


@respx.mock
def test_greenhouse_exhausts_retries_and_raises():
    company = CompanySource(name="Acme", ats_type="greenhouse", board_identifier="acme")
    route = respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs")
    route.mock(return_value=httpx.Response(503))
    collector = make_collector(GreenhouseCollector)
    with pytest.raises(CollectorError):
        collector.fetch_jobs(company)
    assert route.call_count == 3  # max_attempts


@respx.mock
def test_greenhouse_timeout_raises_collector_error():
    company = CompanySource(name="Acme", ats_type="greenhouse", board_identifier="acme")
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").mock(
        side_effect=httpx.TimeoutException("timed out")
    )
    collector = make_collector(GreenhouseCollector)
    with pytest.raises(CollectorError):
        collector.fetch_jobs(company)


@respx.mock
def test_greenhouse_invalid_json_raises_collector_error():
    company = CompanySource(name="Acme", ats_type="greenhouse", board_identifier="acme")
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").mock(
        return_value=httpx.Response(200, content=b"not json")
    )
    collector = make_collector(GreenhouseCollector)
    with pytest.raises(CollectorError):
        collector.fetch_jobs(company)


# ---------- Lever ----------

@respx.mock
def test_lever_happy_path():
    company = CompanySource(name="Acme", ats_type="lever", board_identifier="acme")
    respx.get("https://api.lever.co/v0/postings/acme?mode=json").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": "abc",
                    "text": "Network Security Engineer",
                    "categories": {"location": "Folsom, CA", "team": "IT"},
                    "hostedUrl": "https://jobs.lever.co/acme/abc",
                    "createdAt": 1700000000000,
                }
            ],
        )
    )
    collector = make_collector(LeverCollector)
    jobs = collector.fetch_jobs(company)
    assert len(jobs) == 1
    assert jobs[0].title == "Network Security Engineer"
    assert jobs[0].location_text == "Folsom, CA"
    assert jobs[0].department == "IT"


@respx.mock
def test_lever_unexpected_shape_raises():
    company = CompanySource(name="Acme", ats_type="lever", board_identifier="acme")
    respx.get("https://api.lever.co/v0/postings/acme?mode=json").mock(
        return_value=httpx.Response(200, json={"not": "a list"})
    )
    collector = make_collector(LeverCollector)
    with pytest.raises(CollectorError):
        collector.fetch_jobs(company)


# ---------- Ashby ----------

@respx.mock
def test_ashby_happy_path_with_dict_location():
    company = CompanySource(name="Acme", ats_type="ashby", board_identifier="acme")
    respx.get("https://api.ashbyhq.com/posting-api/job-board/acme?includeCompensation=true").mock(
        return_value=httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": "xyz",
                        "title": "OT Cybersecurity Analyst",
                        "location": {"name": "South San Francisco, CA"},
                        "jobUrl": "https://jobs.ashbyhq.com/acme/xyz",
                        "publishedAt": "2026-02-01",
                        "department": "Security",
                    }
                ]
            },
        )
    )
    collector = make_collector(AshbyCollector)
    jobs = collector.fetch_jobs(company)
    assert len(jobs) == 1
    assert jobs[0].location_text == "South San Francisco, CA"


@respx.mock
def test_ashby_happy_path_with_string_location():
    company = CompanySource(name="Acme", ats_type="ashby", board_identifier="acme")
    respx.get("https://api.ashbyhq.com/posting-api/job-board/acme?includeCompensation=true").mock(
        return_value=httpx.Response(
            200,
            json={"jobs": [{"id": "xyz", "title": "Analyst", "location": "Remote - US"}]},
        )
    )
    collector = make_collector(AshbyCollector)
    jobs = collector.fetch_jobs(company)
    assert jobs[0].location_text == "Remote - US"


def test_ashby_missing_board_identifier_raises():
    company = CompanySource(name="Acme", ats_type="ashby", board_identifier=None)
    collector = make_collector(AshbyCollector)
    with pytest.raises(CollectorError):
        collector.fetch_jobs(company)


@respx.mock
def test_ashby_unexpected_shape_raises():
    company = CompanySource(name="Acme", ats_type="ashby", board_identifier="acme")
    respx.get("https://api.ashbyhq.com/posting-api/job-board/acme?includeCompensation=true").mock(
        return_value=httpx.Response(200, json={"unexpected": "shape"})
    )
    collector = make_collector(AshbyCollector)
    with pytest.raises(CollectorError):
        collector.fetch_jobs(company)


@respx.mock
def test_ashby_skips_malformed_entries():
    company = CompanySource(name="Acme", ats_type="ashby", board_identifier="acme")
    respx.get("https://api.ashbyhq.com/posting-api/job-board/acme?includeCompensation=true").mock(
        return_value=httpx.Response(
            200, json={"jobs": [{"no_id_or_title": True}, {"id": "xyz", "title": "Analyst"}]}
        )
    )
    collector = make_collector(AshbyCollector)
    jobs = collector.fetch_jobs(company)
    assert len(jobs) == 1
    assert jobs[0].external_id == "xyz"


def test_lever_missing_board_identifier_raises():
    company = CompanySource(name="Acme", ats_type="lever", board_identifier=None)
    collector = make_collector(LeverCollector)
    with pytest.raises(CollectorError):
        collector.fetch_jobs(company)


# ---------- Government (USAJOBS) ----------

def test_government_missing_credentials_raises(monkeypatch):
    monkeypatch.delenv("USAJOBS_API_KEY", raising=False)
    monkeypatch.delenv("USAJOBS_EMAIL", raising=False)
    company = CompanySource(name="Some Agency", ats_type="government", board_identifier="cybersecurity")
    collector = make_collector(GovernmentCollector)
    with pytest.raises(CollectorError, match="USAJOBS_API_KEY"):
        collector.fetch_jobs(company)


def test_government_unsupported_api_raises(monkeypatch):
    monkeypatch.setenv("USAJOBS_API_KEY", "key")
    monkeypatch.setenv("USAJOBS_EMAIL", "me@example.com")
    company = CompanySource(
        name="Some Agency", ats_type="government", board_identifier="x", custom_config={"api": "calcareers"}
    )
    collector = make_collector(GovernmentCollector)
    with pytest.raises(CollectorError, match="unsupported government api"):
        collector.fetch_jobs(company)


@respx.mock
def test_government_usajobs_happy_path(monkeypatch):
    monkeypatch.setenv("USAJOBS_API_KEY", "key")
    monkeypatch.setenv("USAJOBS_EMAIL", "me@example.com")
    company = CompanySource(name="DHS", ats_type="government", board_identifier="cybersecurity")
    respx.get("https://data.usajobs.gov/api/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "SearchResult": {
                    "SearchResultItems": [
                        {
                            "MatchedObjectId": "123",
                            "MatchedObjectDescriptor": {
                                "PositionTitle": "IT Specialist (SECURITY)",
                                "PositionLocation": [{"LocationName": "Sacramento, CA"}],
                                "ApplyURI": ["https://usajobs.gov/apply/123"],
                                "PositionURI": "https://usajobs.gov/job/123",
                                "PublicationStartDate": "2026-01-01",
                                "DepartmentName": "Department of Homeland Security",
                            },
                        }
                    ]
                }
            },
        )
    )
    collector = make_collector(GovernmentCollector)
    jobs = collector.fetch_jobs(company)
    assert len(jobs) == 1
    assert jobs[0].title == "IT Specialist (SECURITY)"
    assert jobs[0].location_text == "Sacramento, CA"


@respx.mock
def test_government_unexpected_shape_raises(monkeypatch):
    monkeypatch.setenv("USAJOBS_API_KEY", "key")
    monkeypatch.setenv("USAJOBS_EMAIL", "me@example.com")
    company = CompanySource(name="DHS", ats_type="government", board_identifier="cybersecurity")
    respx.get("https://data.usajobs.gov/api/search").mock(return_value=httpx.Response(200, json={"unexpected": "shape"}))
    collector = make_collector(GovernmentCollector)
    with pytest.raises(CollectorError):
        collector.fetch_jobs(company)


@respx.mock
def test_government_skips_malformed_items(monkeypatch):
    monkeypatch.setenv("USAJOBS_API_KEY", "key")
    monkeypatch.setenv("USAJOBS_EMAIL", "me@example.com")
    company = CompanySource(name="DHS", ats_type="government", board_identifier="cybersecurity")
    respx.get("https://data.usajobs.gov/api/search").mock(
        return_value=httpx.Response(
            200,
            json={"SearchResult": {"SearchResultItems": [{"NoMatchedObjectDescriptor": True}]}},
        )
    )
    collector = make_collector(GovernmentCollector)
    jobs = collector.fetch_jobs(company)
    assert jobs == []


@respx.mock
def test_government_passes_organization_and_location_params(monkeypatch):
    monkeypatch.setenv("USAJOBS_API_KEY", "key")
    monkeypatch.setenv("USAJOBS_EMAIL", "me@example.com")
    company = CompanySource(
        name="DHS", ats_type="government", board_identifier="cybersecurity",
        custom_config={"organization": "DHS", "location_name": "Sacramento, CA", "who_may_apply": "public"},
    )
    route = respx.get("https://data.usajobs.gov/api/search").mock(
        return_value=httpx.Response(200, json={"SearchResult": {"SearchResultItems": []}})
    )
    collector = make_collector(GovernmentCollector)
    collector.fetch_jobs(company)
    request = route.calls[0].request
    assert "Organization=DHS" in str(request.url)
    assert "WhoMayApply=public" in str(request.url)


# ---------- Custom page ----------

@respx.mock
def test_custom_page_happy_path():
    html = """
    <html><body>
      <div class="job-listing">
        <h3 class="job-title">Manufacturing Systems Analyst</h3>
        <a class="job-link" href="/careers/123">Apply</a>
        <span class="job-location">Vacaville, CA</span>
      </div>
    </body></html>
    """
    company = CompanySource(
        name="Acme Biotech",
        ats_type="custom",
        careers_url="https://acmebiotech.com/careers",
        custom_config={
            "job_selector": "div.job-listing",
            "title_selector": "h3.job-title",
            "link_selector": "a.job-link",
            "location_selector": "span.job-location",
        },
    )
    respx.get("https://acmebiotech.com/careers").mock(return_value=httpx.Response(200, text=html))
    collector = make_collector(CustomPageCollector)
    jobs = collector.fetch_jobs(company)
    assert len(jobs) == 1
    assert jobs[0].title == "Manufacturing Systems Analyst"
    assert jobs[0].apply_url == "https://acmebiotech.com/careers/123"
    assert jobs[0].location_text == "Vacaville, CA"


def test_custom_page_missing_selectors_raises():
    company = CompanySource(
        name="Acme", ats_type="custom", careers_url="https://acme.com/careers", custom_config={}
    )
    collector = make_collector(CustomPageCollector)
    with pytest.raises(CollectorError, match="custom_config must define"):
        collector.fetch_jobs(company)


def test_custom_page_missing_careers_url_raises():
    company = CompanySource(name="Acme", ats_type="custom", careers_url=None)
    collector = make_collector(CustomPageCollector)
    with pytest.raises(CollectorError):
        collector.fetch_jobs(company)


# ---------- Per-source failure isolation ----------

@respx.mock
def test_safe_collect_isolates_failure_without_raising():
    company = CompanySource(name="Broken Co", ats_type="greenhouse", board_identifier="brokenco")
    respx.get("https://boards-api.greenhouse.io/v1/boards/brokenco/jobs").mock(return_value=httpx.Response(500))
    collector = make_collector(GreenhouseCollector)
    result = collector.safe_collect(company)
    assert result.ok is False
    assert result.jobs == []
    assert "Broken Co" not in ""  # sanity no-op; real assertion below
    assert result.company_name == "Broken Co"
    assert result.error is not None


@respx.mock
def test_safe_collect_ok_on_success():
    company = CompanySource(name="Acme", ats_type="greenhouse", board_identifier="acme")
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").mock(
        return_value=httpx.Response(200, json={"jobs": []})
    )
    collector = make_collector(GreenhouseCollector)
    result = collector.safe_collect(company)
    assert result.ok is True
    assert result.error is None


def test_safe_collect_isolates_unexpected_exception(monkeypatch):
    company = CompanySource(name="Acme", ats_type="greenhouse", board_identifier="acme")
    collector = make_collector(GreenhouseCollector)

    def boom(_company):
        raise RuntimeError("something broke")

    monkeypatch.setattr(collector, "fetch_jobs", boom)
    result = collector.safe_collect(company)
    assert result.ok is False
    assert "something broke" in result.error


# ---------- Workday ----------

WORKDAY_SEARCH_URL = "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/External/jobs"


def workday_company(**overrides):
    defaults = dict(
        name="Acme",
        ats_type="workday",
        board_identifier="acme",
        custom_config={"wd_host": "wd1", "site": "External"},
    )
    defaults.update(overrides)
    return CompanySource(**defaults)


def workday_posting(n: int) -> dict:
    return {
        "title": f"Security Analyst {n}",
        "externalPath": f"/job/Sacramento-CA/Security-Analyst_{n}-REQ{n}",
        "locationsText": "Sacramento, CA",
        "postedOn": "Posted 1 Day Ago",
    }


@respx.mock
def test_workday_happy_path_single_page():
    company = workday_company()
    respx.post(WORKDAY_SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"total": 1, "jobPostings": [workday_posting(1)]})
    )
    collector = make_collector(WorkdayCollector)
    jobs = collector.fetch_jobs(company)
    assert len(jobs) == 1
    assert jobs[0].title == "Security Analyst 1"
    assert jobs[0].external_id == "/job/Sacramento-CA/Security-Analyst_1-REQ1"
    assert jobs[0].apply_url == "https://acme.wd1.myworkdayjobs.com/External/job/Sacramento-CA/Security-Analyst_1-REQ1"
    assert jobs[0].description_html is None  # fetched lazily, not eagerly


@respx.mock
def test_workday_pagination_survives_zero_total_on_later_pages():
    """Regression test: some Workday tenants report total=0 (not the real
    total) on every page after the first, even though postings keep coming.
    The collector must keep the first non-zero total, not overwrite it."""
    company = workday_company()
    route = respx.post(WORKDAY_SEARCH_URL)
    route.side_effect = [
        httpx.Response(200, json={"total": 45, "jobPostings": [workday_posting(i) for i in range(20)]}),
        httpx.Response(200, json={"total": 0, "jobPostings": [workday_posting(i) for i in range(20, 40)]}),
        httpx.Response(200, json={"total": 0, "jobPostings": [workday_posting(i) for i in range(40, 45)]}),
    ]
    collector = make_collector(WorkdayCollector)
    jobs = collector.fetch_jobs(company)
    assert len(jobs) == 45
    assert route.call_count == 3


@respx.mock
def test_workday_stops_when_postings_empty():
    company = workday_company()
    route = respx.post(WORKDAY_SEARCH_URL)
    route.side_effect = [
        httpx.Response(200, json={"total": 999, "jobPostings": [workday_posting(1)]}),
        httpx.Response(200, json={"total": 0, "jobPostings": []}),
    ]
    collector = make_collector(WorkdayCollector)
    jobs = collector.fetch_jobs(company)
    assert len(jobs) == 1
    assert route.call_count == 2


def test_workday_missing_custom_config_raises():
    company = workday_company(custom_config={})
    collector = make_collector(WorkdayCollector)
    with pytest.raises(CollectorError, match="custom_config"):
        collector.fetch_jobs(company)


def test_workday_missing_board_identifier_raises():
    company = workday_company(board_identifier=None)
    collector = make_collector(WorkdayCollector)
    with pytest.raises(CollectorError):
        collector.fetch_jobs(company)


@respx.mock
def test_workday_unexpected_shape_raises():
    company = workday_company()
    respx.post(WORKDAY_SEARCH_URL).mock(return_value=httpx.Response(200, json={"unexpected": "shape"}))
    collector = make_collector(WorkdayCollector)
    with pytest.raises(CollectorError):
        collector.fetch_jobs(company)


@respx.mock
def test_workday_skips_malformed_postings():
    company = workday_company()
    respx.post(WORKDAY_SEARCH_URL).mock(
        return_value=httpx.Response(
            200, json={"total": 2, "jobPostings": [{"no_path_or_title": True}, workday_posting(1)]}
        )
    )
    collector = make_collector(WorkdayCollector)
    jobs = collector.fetch_jobs(company)
    assert len(jobs) == 1


@respx.mock
def test_workday_fetch_job_description():
    company = workday_company()
    detail_url = "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/External/job/Sacramento-CA/Security-Analyst_1-REQ1"
    respx.get(detail_url).mock(
        return_value=httpx.Response(200, json={"jobPostingInfo": {"jobDescription": "<p>Do security things</p>"}})
    )
    collector = make_collector(WorkdayCollector)
    description = collector.fetch_job_description(company, "/job/Sacramento-CA/Security-Analyst_1-REQ1")
    assert description == "<p>Do security things</p>"


@respx.mock
def test_workday_404_raises_collector_error():
    company = workday_company()
    respx.post(WORKDAY_SEARCH_URL).mock(return_value=httpx.Response(404))
    collector = make_collector(WorkdayCollector)
    with pytest.raises(CollectorError):
        collector.fetch_jobs(company)


# ---------- Amazon ----------

AMAZON_SEARCH_URL = "https://www.amazon.jobs/en/search.json"


def amazon_posting(n: int, location: str = "Fredericksburg, Virginia, USA") -> dict:
    return {
        "id_icims": 1000 + n,
        "title": f"Data Center Engineer {n}",
        "normalized_location": location,
        "description": "Own the design of AWS infrastructure.",
        "basic_qualifications": "Bachelor's degree required.",
        "preferred_qualifications": "5+ years experience.",
        "job_path": f"/en/jobs/{1000 + n}/data-center-engineer",
        "url_next_step": f"https://account.amazon.jobs/jobs/{1000 + n}/apply",
        "posted_date": "July 28, 2026",
        "job_category": "Operations, IT, & Support Engineering",
        "job_family": "Tech Ops Engineering",
    }


@respx.mock
def test_amazon_happy_path_single_page():
    company = CompanySource(name="Amazon", ats_type="amazon")
    respx.get(AMAZON_SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"jobs": [amazon_posting(1)]})
    )
    collector = make_collector(AmazonCollector)
    jobs = collector.fetch_jobs(company)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.external_id == "1001"
    assert job.title == "Data Center Engineer 1"
    assert job.location_text == "Fredericksburg, Virginia, USA"
    assert job.apply_url == "https://account.amazon.jobs/jobs/1001/apply"
    assert "AWS infrastructure" in job.description_html
    assert "Bachelor's degree" in job.description_html
    assert job.department == "Operations, IT, & Support Engineering"


@respx.mock
def test_amazon_falls_back_to_job_path_when_no_apply_url():
    company = CompanySource(name="Amazon", ats_type="amazon")
    posting = amazon_posting(1)
    del posting["url_next_step"]
    respx.get(AMAZON_SEARCH_URL).mock(return_value=httpx.Response(200, json={"jobs": [posting]}))
    collector = make_collector(AmazonCollector)
    jobs = collector.fetch_jobs(company)
    assert jobs[0].apply_url == "https://www.amazon.jobs/en/jobs/1001/data-center-engineer"


@respx.mock
def test_amazon_stops_when_page_smaller_than_page_size():
    """A page shorter than PAGE_SIZE means we've reached the end -- the
    collector shouldn't request another page after that."""
    import src.collectors.amazon as amazon_module

    company = CompanySource(name="Amazon", ats_type="amazon")
    route = respx.get(AMAZON_SEARCH_URL)
    route.mock(return_value=httpx.Response(200, json={"jobs": [amazon_posting(1), amazon_posting(2)]}))
    collector = make_collector(AmazonCollector)
    jobs = collector.fetch_jobs(company)
    assert len(jobs) == 2
    assert route.call_count == 1
    assert amazon_module.PAGE_SIZE > 2  # sanity check the test fixture is actually shorter than a full page


@respx.mock
def test_amazon_pagination_continues_on_full_pages(monkeypatch):
    import src.collectors.amazon as amazon_module

    monkeypatch.setattr(amazon_module, "PAGE_SIZE", 2)
    company = CompanySource(name="Amazon", ats_type="amazon")
    route = respx.get(AMAZON_SEARCH_URL)
    route.side_effect = [
        httpx.Response(200, json={"jobs": [amazon_posting(1), amazon_posting(2)]}),
        httpx.Response(200, json={"jobs": [amazon_posting(3)]}),
    ]
    collector = make_collector(AmazonCollector)
    jobs = collector.fetch_jobs(company)
    assert len(jobs) == 3
    assert route.call_count == 2


@respx.mock
def test_amazon_respects_max_pages_cap(monkeypatch):
    import src.collectors.amazon as amazon_module

    monkeypatch.setattr(amazon_module, "PAGE_SIZE", 1)
    monkeypatch.setattr(amazon_module, "MAX_PAGES", 3)
    company = CompanySource(name="Amazon", ats_type="amazon")
    route = respx.get(AMAZON_SEARCH_URL)
    route.mock(return_value=httpx.Response(200, json={"jobs": [amazon_posting(1)]}))  # always a "full" page
    collector = make_collector(AmazonCollector)
    jobs = collector.fetch_jobs(company)
    assert route.call_count == 3
    assert len(jobs) == 3


@respx.mock
def test_amazon_skips_malformed_postings():
    company = CompanySource(name="Amazon", ats_type="amazon")
    respx.get(AMAZON_SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"jobs": [{"no_id_or_title": True}, amazon_posting(1)]})
    )
    collector = make_collector(AmazonCollector)
    jobs = collector.fetch_jobs(company)
    assert len(jobs) == 1


@respx.mock
def test_amazon_unexpected_shape_raises():
    company = CompanySource(name="Amazon", ats_type="amazon")
    respx.get(AMAZON_SEARCH_URL).mock(return_value=httpx.Response(200, json={"unexpected": "shape"}))
    collector = make_collector(AmazonCollector)
    with pytest.raises(CollectorError):
        collector.fetch_jobs(company)


@respx.mock
def test_amazon_500_raises_collector_error():
    company = CompanySource(name="Amazon", ats_type="amazon")
    respx.get(AMAZON_SEARCH_URL).mock(return_value=httpx.Response(500))
    collector = make_collector(AmazonCollector)
    with pytest.raises(CollectorError):
        collector.fetch_jobs(company)


# ---------- Collector registry ----------

def test_get_collector_class_unknown_ats_type_raises():
    from src.collectors import get_collector_class

    with pytest.raises(ValueError, match="No collector registered"):
        get_collector_class("workday_but_misspelled")


# ---------- Company loading ----------

def test_load_companies_from_yaml():
    from src.collectors.base import load_companies

    companies = load_companies()
    assert len(companies) > 100
    assert all(c.active for c in companies)
    assert all(
        c.ats_type in ("greenhouse", "lever", "ashby", "workday", "government", "custom", "amazon")
        for c in companies
    )
    names = {c.name for c in companies}
    assert "Cloudflare" in names
    assert "Anthropic" in names
    assert "Lonza" in names
    assert "Intel" in names

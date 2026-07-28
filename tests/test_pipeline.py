import httpx
import pytest
import respx

from src.collectors.base import CompanySource
from src.database.models import Company, Job, JobMatch, PipelineRun
from src.database.session import get_session
from src.settings import get_settings
import src.database.session as session_module
import src.services.pipeline as pipeline_module


@pytest.fixture(autouse=True)
def isolated_test_database(tmp_path, monkeypatch):
    """Points the DB at a fresh temp file per test and resets the module-
    level engine/session-factory singletons so each test starts clean."""
    db_path = tmp_path / "test_job_radar.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    monkeypatch.setattr(session_module, "_engine", None)
    monkeypatch.setattr(session_module, "_SessionLocal", None)
    from src.database.session import init_db

    init_db()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def no_real_job_board_export(monkeypatch):
    """The pipeline calls out to a real git repo on disk (see
    src/services/job_board_export.py) after every full run -- tests must
    never perform that real filesystem/git side effect, so replace it with a
    no-op everywhere in this file."""
    monkeypatch.setattr(pipeline_module, "export_job_board", lambda output_dir: None)


@pytest.fixture
def one_company(monkeypatch):
    company = CompanySource(name="Acme", ats_type="greenhouse", board_identifier="acme", industry="cybersecurity")
    monkeypatch.setattr(pipeline_module, "load_companies", lambda: [company])
    return company


def _mock_greenhouse_response(jobs: list[dict]):
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true").mock(
        return_value=httpx.Response(200, json={"jobs": jobs})
    )


GREENHOUSE_JOB = {
    "id": 1,
    "title": "Cybersecurity Analyst",
    "location": {"name": "Sacramento, CA"},
    "content": "<p>Requirements: Splunk and SIEM experience required.</p>",
    "absolute_url": "https://acme.com/jobs/1",
    "updated_at": "2026-07-24T00:00:00-04:00",
    "departments": [],
    "offices": [],
}


@respx.mock
def test_run_pipeline_persists_company_job_and_match(one_company):
    _mock_greenhouse_response([GREENHOUSE_JOB])

    stats = pipeline_module.run_pipeline()

    assert stats.companies_processed == 1
    assert stats.companies_failed == 0
    assert stats.new_jobs == 1
    assert stats.eligible_jobs == 1
    assert stats.scored_jobs == 1

    with get_session() as session:
        companies = session.query(Company).all()
        jobs = session.query(Job).all()
        matches = session.query(JobMatch).all()
        runs = session.query(PipelineRun).all()

        assert len(companies) == 1
        assert companies[0].name == "Acme"
        assert len(jobs) == 1
        assert jobs[0].title == "Cybersecurity Analyst"
        assert jobs[0].external_id == "1"
        assert len(matches) == 1
        assert matches[0].total_score > 0
        assert len(runs) == 1
        assert runs[0].new_jobs == 1


@respx.mock
def test_run_pipeline_rerun_updates_existing_job_not_duplicates(one_company):
    _mock_greenhouse_response([GREENHOUSE_JOB])
    pipeline_module.run_pipeline()

    updated_job = dict(GREENHOUSE_JOB)
    updated_job["title"] = "Senior Cybersecurity Analyst"  # simulate a re-scrape with a title tweak
    _mock_greenhouse_response([updated_job])
    stats_second_run = pipeline_module.run_pipeline()

    assert stats_second_run.new_jobs == 0
    assert stats_second_run.updated_jobs == 1

    with get_session() as session:
        jobs = session.query(Job).all()
        assert len(jobs) == 1  # not duplicated
        assert jobs[0].title == "Senior Cybersecurity Analyst"  # updated in place


@respx.mock
def test_run_pipeline_isolates_per_company_failure(monkeypatch):
    good = CompanySource(name="Acme", ats_type="greenhouse", board_identifier="acme")
    bad = CompanySource(name="Broken Co", ats_type="greenhouse", board_identifier="brokenco")
    monkeypatch.setattr(pipeline_module, "load_companies", lambda: [good, bad])

    _mock_greenhouse_response([GREENHOUSE_JOB])
    respx.get("https://boards-api.greenhouse.io/v1/boards/brokenco/jobs?content=true").mock(
        return_value=httpx.Response(500)
    )

    stats = pipeline_module.run_pipeline()

    assert stats.companies_processed == 1
    assert stats.companies_failed == 1
    assert len(stats.errors) == 1
    assert "Broken Co" in stats.errors[0]
    assert stats.new_jobs == 1  # Acme's job still made it through


@respx.mock
def test_run_pipeline_isolates_collector_construction_failure(monkeypatch):
    """A second, outer layer of isolation: if the collector itself blows up
    constructing/entering (not just fetch_jobs, which safe_collect already
    guards), one company still can't take down the whole run."""
    good = CompanySource(name="Acme", ats_type="greenhouse", board_identifier="acme")
    broken = CompanySource(name="Broken Co", ats_type="lever", board_identifier="brokenco")
    monkeypatch.setattr(pipeline_module, "load_companies", lambda: [good, broken])
    _mock_greenhouse_response([GREENHOUSE_JOB])

    from src.collectors.lever import LeverCollector

    def exploding_init(self, *args, **kwargs):
        raise RuntimeError("simulated construction failure")

    monkeypatch.setattr(LeverCollector, "__init__", exploding_init)

    stats = pipeline_module.run_pipeline()

    assert stats.companies_failed == 1
    assert any("Broken Co" in e for e in stats.errors)
    assert stats.new_jobs == 1  # Acme still processed fine


@respx.mock
def test_run_pipeline_source_filter(monkeypatch):
    acme = CompanySource(name="Acme", ats_type="greenhouse", board_identifier="acme")
    beta = CompanySource(name="Beta", ats_type="lever", board_identifier="beta")
    monkeypatch.setattr(pipeline_module, "load_companies", lambda: [acme, beta])
    _mock_greenhouse_response([GREENHOUSE_JOB])

    stats = pipeline_module.run_pipeline(source_filter="greenhouse")

    assert stats.companies_processed == 1
    with get_session() as session:
        companies = session.query(Company).all()
        assert len(companies) == 1
        assert companies[0].name == "Acme"


@respx.mock
def test_run_pipeline_dry_run_does_not_write_to_db(one_company):
    _mock_greenhouse_response([GREENHOUSE_JOB])

    stats = pipeline_module.run_pipeline(dry_run=True)

    assert stats.eligible_jobs == 1
    with get_session() as session:
        assert session.query(Job).count() == 0
        assert session.query(PipelineRun).count() == 0


@respx.mock
def test_run_pipeline_ineligible_job_not_scored(one_company):
    senior_job = dict(GREENHOUSE_JOB)
    senior_job["title"] = "Senior Cybersecurity Manager"
    _mock_greenhouse_response([senior_job])

    stats = pipeline_module.run_pipeline()

    assert stats.new_jobs == 1  # job is still stored
    assert stats.eligible_jobs == 0
    assert stats.scored_jobs == 0
    with get_session() as session:
        assert session.query(JobMatch).count() == 0


@respx.mock
def test_run_pipeline_never_alerts_twice_for_same_job(one_company, monkeypatch):
    """Alert deduplication: a job discovered in run 1 must not trigger a
    second alert in run 2 just because the pipeline re-scores it (e.g. the
    posting is still live and gets updated_jobs, not new_jobs)."""

    class RecordingProvider:
        def __init__(self):
            self.sent = []

        def send(self, notification):
            self.sent.append(notification)
            return True

    provider = RecordingProvider()
    monkeypatch.setattr(pipeline_module, "get_alert_provider", lambda settings: provider)

    high_score_job = dict(GREENHOUSE_JOB)
    high_score_job["content"] = "<p>Requirements: Splunk, SIEM, Palo Alto NGFW, Panorama, Network Security required.</p>"
    _mock_greenhouse_response([high_score_job])

    stats_first_run = pipeline_module.run_pipeline()
    assert stats_first_run.new_jobs == 1
    alerts_after_first_run = len(provider.sent)
    assert alerts_after_first_run >= 1  # a strong match should have alerted

    # Second run: same job, same content — re-scraped and updated, not new.
    _mock_greenhouse_response([high_score_job])
    stats_second_run = pipeline_module.run_pipeline()
    assert stats_second_run.new_jobs == 0
    assert stats_second_run.updated_jobs == 1
    assert len(provider.sent) == alerts_after_first_run  # no additional alert sent


@respx.mock
def test_run_pipeline_company_name_filter(monkeypatch):
    acme = CompanySource(name="Acme", ats_type="greenhouse", board_identifier="acme")
    other = CompanySource(name="Other Co", ats_type="greenhouse", board_identifier="otherco")
    monkeypatch.setattr(pipeline_module, "load_companies", lambda: [acme, other])
    _mock_greenhouse_response([GREENHOUSE_JOB])

    stats = pipeline_module.run_pipeline(company_names=["Acme"])

    assert stats.companies_processed == 1
    with get_session() as session:
        companies = session.query(Company).all()
        assert len(companies) == 1
        assert companies[0].name == "Acme"


@respx.mock
def test_run_pipeline_expires_jobs_no_longer_on_the_board(one_company):
    job_a = dict(GREENHOUSE_JOB, id=1)
    job_b = dict(GREENHOUSE_JOB, id=2, title="Second Analyst", absolute_url="https://acme.com/jobs/2")
    _mock_greenhouse_response([job_a, job_b])
    pipeline_module.run_pipeline()

    with get_session() as session:
        jobs = {j.external_id: j.is_active for j in session.query(Job).all()}
        assert jobs == {"1": True, "2": True}

    # Second scrape: job 2 was pulled down, only job 1 remains on the board.
    _mock_greenhouse_response([job_a])
    stats = pipeline_module.run_pipeline()
    assert stats.expired_jobs == 1

    with get_session() as session:
        jobs = {j.external_id: j.is_active for j in session.query(Job).all()}
        assert jobs == {"1": True, "2": False}


@respx.mock
def test_run_pipeline_does_not_expire_when_limit_applied(one_company):
    """--limit truncates what a run actually sees — it must never be used
    as a signal that the rest of the board's jobs disappeared."""
    job_a = dict(GREENHOUSE_JOB, id=1)
    job_b = dict(GREENHOUSE_JOB, id=2, title="Second Analyst", absolute_url="https://acme.com/jobs/2")
    _mock_greenhouse_response([job_a, job_b])
    pipeline_module.run_pipeline()

    _mock_greenhouse_response([job_a, job_b])
    stats = pipeline_module.run_pipeline(jobs_per_company_limit=1)  # only "sees" job_a this run
    assert stats.expired_jobs == 0

    with get_session() as session:
        jobs = {j.external_id: j.is_active for j in session.query(Job).all()}
        assert jobs == {"1": True, "2": True}  # job 2 NOT expired despite not being in the truncated batch


@respx.mock
def test_run_pipeline_alerts_on_repeated_total_failure(monkeypatch):
    bad_company = CompanySource(name="Broken Co", ats_type="greenhouse", board_identifier="brokenco")
    monkeypatch.setattr(pipeline_module, "load_companies", lambda: [bad_company])
    respx.get("https://boards-api.greenhouse.io/v1/boards/brokenco/jobs?content=true").mock(
        return_value=httpx.Response(500)
    )

    class RecordingProvider:
        def __init__(self):
            self.sent = []

        def send(self, notification):
            self.sent.append(notification)
            return True

    provider = RecordingProvider()
    monkeypatch.setattr(pipeline_module, "get_alert_provider", lambda settings: provider)

    stats1 = pipeline_module.run_pipeline()
    assert stats1.repeated_failure_alert_sent is False  # only 1 failed run so far

    stats2 = pipeline_module.run_pipeline()
    assert stats2.repeated_failure_alert_sent is False  # 2 failed runs — still below threshold

    stats3 = pipeline_module.run_pipeline()
    assert stats3.repeated_failure_alert_sent is True  # 3rd consecutive zero-progress run
    assert any("repeatedly failing" in n.title for n in provider.sent)


@respx.mock
def test_run_pipeline_no_failure_alert_when_some_jobs_found(one_company, monkeypatch):
    """A handful of individual company failures alongside real progress is
    normal per-source isolation — it must NOT trigger the repeated-failure
    alert."""
    class RecordingProvider:
        def __init__(self):
            self.sent = []

        def send(self, notification):
            self.sent.append(notification)
            return True

    provider = RecordingProvider()
    monkeypatch.setattr(pipeline_module, "get_alert_provider", lambda settings: provider)

    _mock_greenhouse_response([GREENHOUSE_JOB])
    for _ in range(3):
        stats = pipeline_module.run_pipeline()
    assert stats.repeated_failure_alert_sent is False
    assert not any("repeatedly failing" in n.title for n in provider.sent)

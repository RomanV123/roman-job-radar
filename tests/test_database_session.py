import pytest

import src.database.session as session_module
from src.database.models import Application, Company, Job, JobMatch, PipelineRun
from src.database.session import _redact_credentials, delete_all_data, get_engine, get_session, init_db
from src.settings import get_settings


@pytest.fixture(autouse=True)
def isolated_engine(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    monkeypatch.setattr(session_module, "_engine", None)
    monkeypatch.setattr(session_module, "_SessionLocal", None)
    yield
    get_settings.cache_clear()


def test_get_session_commits_on_success():
    init_db()
    with get_session() as session:
        session.add(Company(name="Acme", ats_type="greenhouse", board_identifier="acme"))

    with get_session() as session:
        assert session.query(Company).count() == 1


def test_get_session_rolls_back_on_exception():
    init_db()
    with pytest.raises(RuntimeError):
        with get_session() as session:
            session.add(Company(name="Acme", ats_type="greenhouse", board_identifier="acme"))
            session.flush()
            raise RuntimeError("simulated failure mid-transaction")

    with get_session() as session:
        assert session.query(Company).count() == 0  # rolled back, not partially committed


def test_get_engine_non_sqlite_url_omits_check_same_thread(monkeypatch):
    # postgresql:// would need the psycopg2 driver installed (this project
    # uses SQLite for the MVP) — mock create_engine to verify the branch
    # logic itself without needing that driver present.
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/dbname")
    get_settings.cache_clear()
    monkeypatch.setattr(session_module, "_engine", None)
    monkeypatch.setattr(session_module, "_SessionLocal", None)

    captured = {}

    def fake_create_engine(url, connect_args=None):
        captured["url"] = url
        captured["connect_args"] = connect_args
        return "fake-engine"

    monkeypatch.setattr(session_module, "create_engine", fake_create_engine)

    engine = get_engine()
    assert engine == "fake-engine"
    assert captured["connect_args"] == {}  # no check_same_thread for non-sqlite
    get_settings.cache_clear()


def test_get_engine_is_a_singleton():
    init_db()
    engine1 = get_engine()
    engine2 = get_engine()
    assert engine1 is engine2


def test_redact_credentials_masks_password():
    redacted = _redact_credentials("postgresql://myuser:supersecret@localhost:5432/dbname")
    assert "supersecret" not in redacted
    assert "myuser" in redacted
    assert "***" in redacted


def test_redact_credentials_sqlite_url_unchanged():
    url = "sqlite:///data/job_radar.db"
    assert _redact_credentials(url) == url


def test_redact_credentials_no_password_unchanged():
    url = "postgresql://localhost:5432/dbname"
    assert _redact_credentials(url) == url


def test_delete_all_data_removes_everything():
    init_db()
    with get_session() as session:
        company = Company(name="Acme", ats_type="greenhouse", board_identifier="acme")
        session.add(company)
        session.flush()
        job = Job(external_id="1", source="greenhouse", company_id=company.id, title="Analyst")
        session.add(job)
        session.flush()
        session.add(JobMatch(job_id=job.id, total_score=80.0))
        session.add(Application(job_id=job.id, status="saved"))
        session.add(PipelineRun(source="greenhouse", jobs_found=1, new_jobs=1))

    counts = delete_all_data()

    assert counts == {
        "job_matches": 1,
        "applications": 1,
        "jobs": 1,
        "pipeline_runs": 1,
        "companies": 1,
    }

    with get_session() as session:
        assert session.query(Company).count() == 0
        assert session.query(Job).count() == 0
        assert session.query(JobMatch).count() == 0
        assert session.query(Application).count() == 0
        assert session.query(PipelineRun).count() == 0


def test_delete_all_data_on_empty_database():
    init_db()
    counts = delete_all_data()
    assert all(v == 0 for v in counts.values())

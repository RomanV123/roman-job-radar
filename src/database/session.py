"""Database engine/session management. Swapping SQLite for PostgreSQL later
only requires changing DATABASE_URL — no code here is SQLite-specific."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.database.models import Base
from src.logging_config import get_logger
from src.settings import get_settings

logger = get_logger(__name__)

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _redact_credentials(url: str) -> str:
    """Masks any embedded password before a DATABASE_URL is logged — SQLite
    URLs never carry credentials, but this is a swappable-to-Postgres
    architecture (see module docstring), and a future
    postgresql://user:password@host/db URL must never land in plaintext
    logs."""
    parts = urlsplit(url)
    if not parts.password:
        return url
    netloc = parts.netloc.replace(f":{parts.password}@", ":***@")
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings = get_settings()
        if settings.database_url.startswith("sqlite"):
            db_path = settings.database_url.split("///")[-1]
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            connect_args = {"check_same_thread": False}
        else:
            connect_args = {}
        _engine = create_engine(settings.database_url, connect_args=connect_args)
        logger.info("Database engine created for %s", _redact_credentials(settings.database_url))
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)
    return _SessionLocal


def init_db() -> None:
    """Create all tables that don't exist yet. Idempotent."""
    Base.metadata.create_all(bind=get_engine())
    logger.info("Database schema initialized")


@contextmanager
def get_session() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def delete_all_data() -> dict[str, int]:
    """Permanently deletes every row from every table — jobs, matches,
    applications, companies, and pipeline run history. Irreversible.

    Deliberately does NOT touch config/profile.yaml, config/companies.yaml,
    or resume.pdf — those are user-authored source configuration, not data
    the pipeline collected or generated, so a "delete all data" privacy
    control shouldn't silently erase them.
    """
    from src.database.models import Application, Company, Job, JobMatch, PipelineRun

    counts: dict[str, int] = {}
    with get_session() as session:
        # Children before parents, to respect foreign key constraints.
        for model in (JobMatch, Application, Job, PipelineRun, Company):
            counts[model.__tablename__] = session.query(model).delete()
    logger.warning("Deleted all data: %s", counts)
    return counts

"""SQLAlchemy ORM models for Roman Job Radar.

SQLite for the MVP; kept ORM-only (no raw SQL, no SQLite-specific types in
column definitions) so swapping DATABASE_URL to PostgreSQL later requires no
model changes.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    industry: Mapped[str | None] = mapped_column(String(100))
    ats_type: Mapped[str | None] = mapped_column(String(50))  # greenhouse | lever | ashby | government | custom
    board_identifier: Mapped[str | None] = mapped_column(String(255))
    careers_url: Mapped[str | None] = mapped_column(String(1000))
    priority: Mapped[int] = mapped_column(Integer, default=2)  # 1=hourly, 2=every 3h, 3=discovery
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    jobs: Mapped[list["Job"]] = relationship(back_populates="company")

    __table_args__ = (
        UniqueConstraint("name", "board_identifier", name="uq_company_name_board"),
    )

    def __repr__(self) -> str:
        return f"<Company id={self.id} name={self.name!r} ats={self.ats_type}>"


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(50), nullable=False)  # greenhouse | lever | ashby | government | custom
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False)

    title: Mapped[str] = mapped_column(String(500), nullable=False)
    normalized_title: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)

    location: Mapped[str | None] = mapped_column(String(255))
    state: Mapped[str | None] = mapped_column(String(2))
    workplace_type: Mapped[str | None] = mapped_column(String(20))  # onsite | hybrid | remote
    employment_type: Mapped[str | None] = mapped_column(String(20))  # full_time | internship | contract | ...

    salary_min: Mapped[float | None] = mapped_column(Float)
    salary_max: Mapped[float | None] = mapped_column(Float)
    experience_min: Mapped[float | None] = mapped_column(Float)
    experience_max: Mapped[float | None] = mapped_column(Float)

    posted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    apply_url: Mapped[str | None] = mapped_column(String(1000))
    source_url: Mapped[str | None] = mapped_column(String(1000))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    content_hash: Mapped[str | None] = mapped_column(String(64))

    company: Mapped["Company"] = relationship(back_populates="jobs")
    matches: Mapped[list["JobMatch"]] = relationship(back_populates="job", cascade="all, delete-orphan")
    applications: Mapped[list["Application"]] = relationship(back_populates="job", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_job_source_external_id"),
    )

    def __repr__(self) -> str:
        return f"<Job id={self.id} title={self.title!r} company_id={self.company_id}>"


class JobMatch(Base):
    __tablename__ = "job_matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False)

    total_score: Mapped[float] = mapped_column(Float, nullable=False)
    skills_score: Mapped[float] = mapped_column(Float, default=0.0)
    experience_score: Mapped[float] = mapped_column(Float, default=0.0)
    title_score: Mapped[float] = mapped_column(Float, default=0.0)
    education_score: Mapped[float] = mapped_column(Float, default=0.0)
    location_score: Mapped[float] = mapped_column(Float, default=0.0)
    semantic_score: Mapped[float] = mapped_column(Float, default=0.0)
    freshness_score: Mapped[float] = mapped_column(Float, default=0.0)

    matching_skills: Mapped[str | None] = mapped_column(Text)  # JSON-encoded list
    missing_skills: Mapped[str | None] = mapped_column(Text)  # JSON-encoded list
    match_explanation: Mapped[str | None] = mapped_column(Text)

    evaluated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped["Job"] = relationship(back_populates="matches")

    __table_args__ = (
        UniqueConstraint("job_id", "evaluated_at", name="uq_match_job_evaluated_at"),
    )

    def __repr__(self) -> str:
        return f"<JobMatch id={self.id} job_id={self.job_id} score={self.total_score}>"


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False)

    status: Mapped[str] = mapped_column(String(30), default="saved")  # saved | applied | interviewing | rejected | offer | archived
    date_saved: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    date_applied: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    resume_version: Mapped[str | None] = mapped_column(String(255))
    follow_up_date: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    job: Mapped["Job"] = relationship(back_populates="applications")

    __table_args__ = (
        UniqueConstraint("job_id", name="uq_application_job_id"),
    )

    def __repr__(self) -> str:
        return f"<Application id={self.id} job_id={self.job_id} status={self.status}>"


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str | None] = mapped_column(String(50))  # null = full run across all sources
    jobs_found: Mapped[int] = mapped_column(Integer, default=0)
    new_jobs: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[str | None] = mapped_column(Text)  # JSON-encoded list of error strings

    def __repr__(self) -> str:
        return f"<PipelineRun id={self.id} source={self.source} new_jobs={self.new_jobs}>"

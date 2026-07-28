"""Exports the current job database as a public, category-sectioned markdown
job board into a separate git repo (RomanV123/roman-job-radar-board), then
commits and pushes.

Two views per category:
  - all-jobs.md        every active job the pipeline found in that category,
                        unfiltered by resume fit, most-recent first, capped
                        (see MAX_ALL_JOBS_PER_CATEGORY) so the exported repo
                        doesn't grow without bound.
  - matched-for-me.md   only jobs that passed eligibility and scored at or
                        above MIN_MATCH_SCORE against Roman's resume,
                        highest score first.

Categorization is keyword-based (src/services/job_categorizer.py), separate
from resume scoring -- it answers "which section of the board," not "is
this a good fit for Roman."

Called automatically at the end of every pipeline run (see
src/services/pipeline.py) as well as from scripts/export_job_board.py for
manual/on-demand runs. Failures here are logged, never raised -- a broken
export must not take down the main job-collection pipeline.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from src.database.models import Company, Job
from src.database.session import get_session
from src.logging_config import get_logger
from src.services.dashboard_data import JobRow, load_visible_jobs
from src.services.job_categorizer import CATEGORY_ORDER, categorize_job

logger = get_logger(__name__)

MIN_MATCH_SCORE = 60.0
MAX_ALL_JOBS_PER_CATEGORY = 300
MAX_MATCHED_PER_CATEGORY = 150
FEATURED_CATEGORY = "cybersecurity"

CATEGORY_LABELS = {
    "cybersecurity": "Cybersecurity",
    "software_engineering": "Software Engineering",
    "data_analytics": "Data & Analytics",
    "product_management": "Product Management",
}
CATEGORY_DIRS = {
    "cybersecurity": "cybersecurity",
    "software_engineering": "software-engineering",
    "data_analytics": "data-analytics",
    "product_management": "product-management",
}


@dataclass
class BoardJob:
    title: str
    company: str
    location: str | None
    workplace_type: str | None
    employment_type: str | None
    posted_at: datetime | None
    apply_url: str | None
    score: float | None  # None for unfiltered "all jobs" rows


def _escape(text: str | None) -> str:
    if not text:
        return ""
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _fmt_date(dt: datetime | None) -> str:
    if not dt:
        return ""
    return dt.strftime("%Y-%m-%d")


def _sort_key_posted_at(dt: datetime | None) -> datetime:
    """SQLite doesn't strictly enforce tz-awareness even on a
    DateTime(timezone=True) column, so some rows come back naive -- normalize
    everything to aware UTC before comparing so sort() doesn't blow up."""
    if dt is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _load_all_active_jobs() -> list[tuple[Job, Company]]:
    with get_session() as session:
        rows = session.execute(
            select(Job, Company).join(Company, Company.id == Job.company_id).where(Job.is_active.is_(True))
        ).all()
        return [(job, company) for job, company in rows]


def _load_matched_jobs() -> list[JobRow]:
    with get_session() as session:
        return load_visible_jobs(session, only_active=True)


def _bucket_all_jobs(rows: list[tuple[Job, Company]]) -> dict[str, list[BoardJob]]:
    buckets: dict[str, list[BoardJob]] = {c: [] for c in CATEGORY_ORDER}
    for job, company in rows:
        category = categorize_job(job.title)
        if category is None:
            continue
        buckets[category].append(
            BoardJob(
                title=job.title,
                company=company.name,
                location=job.location,
                workplace_type=job.workplace_type,
                employment_type=job.employment_type,
                posted_at=job.posted_at,
                apply_url=job.apply_url or job.source_url,
                score=None,
            )
        )
    for category, jobs in buckets.items():
        jobs.sort(key=lambda j: _sort_key_posted_at(j.posted_at), reverse=True)
        buckets[category] = jobs[:MAX_ALL_JOBS_PER_CATEGORY]
    return buckets


def _bucket_matched_jobs(rows: list[JobRow]) -> dict[str, list[BoardJob]]:
    buckets: dict[str, list[BoardJob]] = {c: [] for c in CATEGORY_ORDER}
    for row in rows:
        if row.total_score < MIN_MATCH_SCORE:
            continue
        category = categorize_job(row.title)
        if category is None:
            continue
        buckets[category].append(
            BoardJob(
                title=row.title,
                company=row.company_name,
                location=row.location,
                workplace_type=row.workplace_type,
                employment_type=row.employment_type,
                posted_at=row.posted_at,
                apply_url=row.apply_url or row.source_url,
                score=row.total_score,
            )
        )
    for category, jobs in buckets.items():
        jobs.sort(key=lambda j: j.score or 0.0, reverse=True)
        buckets[category] = jobs[:MAX_MATCHED_PER_CATEGORY]
    return buckets


def _render_table(jobs: list[BoardJob], show_score: bool) -> str:
    if not jobs:
        return "_No jobs currently in this list._\n"
    header = "| Score | Title | Company | Location | Type | Posted | Apply |\n" if show_score else \
             "| Title | Company | Location | Type | Posted | Apply |\n"
    sep = "|---|---|---|---|---|---|---|\n" if show_score else "|---|---|---|---|---|---|\n"
    lines = [header, sep]
    for j in jobs:
        wp = _escape(j.workplace_type)
        et = _escape(j.employment_type)
        job_type = ", ".join(x for x in (wp, et) if x)
        link = f"[Apply]({j.apply_url})" if j.apply_url else ""
        if show_score:
            lines.append(
                f"| {j.score:.0f} | {_escape(j.title)} | {_escape(j.company)} | {_escape(j.location)} | "
                f"{job_type} | {_fmt_date(j.posted_at)} | {link} |\n"
            )
        else:
            lines.append(
                f"| {_escape(j.title)} | {_escape(j.company)} | {_escape(j.location)} | "
                f"{job_type} | {_fmt_date(j.posted_at)} | {link} |\n"
            )
    return "".join(lines)


def _write_category_pages(output_dir: Path, category: str, all_jobs: list[BoardJob], matched_jobs: list[BoardJob]) -> None:
    category_dir = output_dir / CATEGORY_DIRS[category]
    category_dir.mkdir(parents=True, exist_ok=True)
    label = CATEGORY_LABELS[category]

    (category_dir / "matched-for-me.md").write_text(
        f"# {label} — Matched for Roman\n\n"
        f"Jobs scored at or above {MIN_MATCH_SCORE:.0f} against Roman's resume, highest score first. "
        f"Showing up to {MAX_MATCHED_PER_CATEGORY}.\n\n"
        + _render_table(matched_jobs, show_score=True),
        encoding="utf-8",
    )
    (category_dir / "all-jobs.md").write_text(
        f"# {label} — All Jobs Found\n\n"
        f"Every active {label.lower()} posting the pipeline has found, unfiltered by resume fit, "
        f"most recently posted first. Showing up to {MAX_ALL_JOBS_PER_CATEGORY} of the total found.\n\n"
        + _render_table(all_jobs, show_score=False),
        encoding="utf-8",
    )


def _write_readme(output_dir: Path, all_buckets: dict[str, list[BoardJob]], matched_buckets: dict[str, list[BoardJob]]) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Roman Job Radar — Job Board\n\n",
        f"Auto-generated by [Roman Job Radar](https://github.com/RomanV123/roman-job-radar) "
        f"every 3 hours. Last updated: **{now}**.\n\n",
        "This board mirrors what the pipeline finds across 500+ companies, sectioned by category. "
        "Each category has two views: every job found (unfiltered), and jobs specifically matched "
        "against Roman's resume.\n\n",
        "## Summary\n\n",
        "| Category | All jobs found | Matched for Roman |\n|---|---|---|\n",
    ]
    for category in CATEGORY_ORDER:
        label = CATEGORY_LABELS[category]
        path = CATEGORY_DIRS[category]
        lines.append(
            f"| [{label}]({path}/all-jobs.md) | {len(all_buckets[category])} | "
            f"[{len(matched_buckets[category])} matches]({path}/matched-for-me.md) |\n"
        )

    lines.append(f"\n## 🔐 Featured: {CATEGORY_LABELS[FEATURED_CATEGORY]} — Top Matches\n\n")
    lines.append(_render_table(matched_buckets[FEATURED_CATEGORY][:20], show_score=True))
    path = CATEGORY_DIRS[FEATURED_CATEGORY]
    lines.append(f"\n[See all {len(matched_buckets[FEATURED_CATEGORY])} cybersecurity matches]({path}/matched-for-me.md) "
                 f"· [See all {len(all_buckets[FEATURED_CATEGORY])} cybersecurity jobs found]({path}/all-jobs.md)\n")

    (output_dir / "README.md").write_text("".join(lines), encoding="utf-8")


def _git_publish(output_dir: Path, push: bool) -> None:
    subprocess.run(["git", "add", "-A"], cwd=output_dir, check=True)
    result = subprocess.run(["git", "status", "--porcelain"], cwd=output_dir, capture_output=True, text=True, check=True)
    if not result.stdout.strip():
        logger.info("Job board export: no changes since last run, skipping commit")
        return
    subprocess.run(
        ["git", "commit", "-m", f"Update job board ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')})"],
        cwd=output_dir,
        check=True,
    )
    if push:
        subprocess.run(["git", "push"], cwd=output_dir, check=True)
    logger.info("Job board export: committed and pushed")


def run_export(output_dir: Path, push: bool = True) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    all_rows = _load_all_active_jobs()
    matched_rows = _load_matched_jobs()

    all_buckets = _bucket_all_jobs(all_rows)
    matched_buckets = _bucket_matched_jobs(matched_rows)

    for category in CATEGORY_ORDER:
        _write_category_pages(output_dir, category, all_buckets[category], matched_buckets[category])
    _write_readme(output_dir, all_buckets, matched_buckets)
    _git_publish(output_dir, push=push)

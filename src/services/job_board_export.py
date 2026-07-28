"""Exports the current job database as a public, category-sectioned markdown
job board into a separate git repo (RomanV123/roman-job-radar-board), then
commits and pushes.

Each category (cybersecurity, software engineering, data & analytics,
product management) is split into full-time and internship sections --
matching the two employment types this project's profile.yaml actually
targets (see config/profile.yaml `employment_types`), so contract/part-time
postings simply aren't part of this board. Each employment-type section has
two views:
  - all-jobs.md        every active job the pipeline found there,
                        unfiltered by resume fit, most-recent first, capped
                        (see MAX_ALL_JOBS_PER_SECTION) so the exported repo
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
MAX_ALL_JOBS_PER_SECTION = 300
MAX_MATCHED_PER_SECTION = 150
FEATURED_CATEGORY = "cybersecurity"

EMPLOYMENT_ORDER = ["full_time", "internship"]
EMPLOYMENT_LABELS = {"full_time": "Full-Time", "internship": "Internships"}
EMPLOYMENT_DIRS = {"full_time": "full-time", "internship": "internships"}

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

# category -> employment_type -> list[BoardJob]
Buckets = dict[str, dict[str, list["BoardJob"]]]


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


def _empty_buckets() -> Buckets:
    return {category: {et: [] for et in EMPLOYMENT_ORDER} for category in CATEGORY_ORDER}


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


def _bucket_all_jobs(rows: list[tuple[Job, Company]]) -> Buckets:
    buckets = _empty_buckets()
    for job, company in rows:
        if job.employment_type not in EMPLOYMENT_ORDER:
            continue  # contract/part-time/unknown aren't part of this board
        category = categorize_job(job.title)
        if category is None:
            continue
        buckets[category][job.employment_type].append(
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
    for category in CATEGORY_ORDER:
        for employment_type in EMPLOYMENT_ORDER:
            jobs = buckets[category][employment_type]
            jobs.sort(key=lambda j: _sort_key_posted_at(j.posted_at), reverse=True)
            buckets[category][employment_type] = jobs[:MAX_ALL_JOBS_PER_SECTION]
    return buckets


def _bucket_matched_jobs(rows: list[JobRow]) -> Buckets:
    buckets = _empty_buckets()
    for row in rows:
        if row.total_score < MIN_MATCH_SCORE:
            continue
        if row.employment_type not in EMPLOYMENT_ORDER:
            continue
        category = categorize_job(row.title)
        if category is None:
            continue
        buckets[category][row.employment_type].append(
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
    for category in CATEGORY_ORDER:
        for employment_type in EMPLOYMENT_ORDER:
            jobs = buckets[category][employment_type]
            jobs.sort(key=lambda j: j.score or 0.0, reverse=True)
            buckets[category][employment_type] = jobs[:MAX_MATCHED_PER_SECTION]
    return buckets


def _render_table(jobs: list[BoardJob], show_score: bool) -> str:
    if not jobs:
        return "_No jobs currently in this list._\n"
    header = "| Score | Title | Company | Location | Workplace | Posted | Apply |\n" if show_score else \
             "| Title | Company | Location | Workplace | Posted | Apply |\n"
    sep = "|---|---|---|---|---|---|---|\n" if show_score else "|---|---|---|---|---|---|\n"
    lines = [header, sep]
    for j in jobs:
        wp = _escape(j.workplace_type)
        link = f"[Apply]({j.apply_url})" if j.apply_url else ""
        if show_score:
            lines.append(
                f"| {j.score:.0f} | {_escape(j.title)} | {_escape(j.company)} | {_escape(j.location)} | "
                f"{wp} | {_fmt_date(j.posted_at)} | {link} |\n"
            )
        else:
            lines.append(
                f"| {_escape(j.title)} | {_escape(j.company)} | {_escape(j.location)} | "
                f"{wp} | {_fmt_date(j.posted_at)} | {link} |\n"
            )
    return "".join(lines)


def _write_section_pages(
    output_dir: Path, category: str, employment_type: str, all_jobs: list[BoardJob], matched_jobs: list[BoardJob]
) -> None:
    section_dir = output_dir / CATEGORY_DIRS[category] / EMPLOYMENT_DIRS[employment_type]
    section_dir.mkdir(parents=True, exist_ok=True)
    category_label = CATEGORY_LABELS[category]
    employment_label = EMPLOYMENT_LABELS[employment_type]

    (section_dir / "matched-for-me.md").write_text(
        f"# {category_label} {employment_label} — Matched for Roman\n\n"
        f"Jobs scored at or above {MIN_MATCH_SCORE:.0f} against Roman's resume, highest score first. "
        f"Showing up to {MAX_MATCHED_PER_SECTION}.\n\n"
        + _render_table(matched_jobs, show_score=True),
        encoding="utf-8",
    )
    (section_dir / "all-jobs.md").write_text(
        f"# {category_label} {employment_label} — All Jobs Found\n\n"
        f"Every active {category_label.lower()} {employment_label.lower()} posting the pipeline has found, "
        f"unfiltered by resume fit, most recently posted first. "
        f"Showing up to {MAX_ALL_JOBS_PER_SECTION} of the total found.\n\n"
        + _render_table(all_jobs, show_score=False),
        encoding="utf-8",
    )


def _write_readme(output_dir: Path, all_buckets: Buckets, matched_buckets: Buckets) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Roman Job Radar — Job Board\n\n",
        f"Auto-generated by [Roman Job Radar](https://github.com/RomanV123/roman-job-radar) "
        f"every 3 hours. Last updated: **{now}**.\n\n",
        "This board mirrors what the pipeline finds across 500+ companies, sectioned by category and "
        "by employment type (full-time / internship). Each section has two views: every job found "
        "(unfiltered), and jobs specifically matched against Roman's resume.\n\n",
        "## Summary\n\n",
        "| Category | Type | All jobs found | Matched for Roman |\n|---|---|---|---|\n",
    ]
    for category in CATEGORY_ORDER:
        category_label = CATEGORY_LABELS[category]
        category_path = CATEGORY_DIRS[category]
        for employment_type in EMPLOYMENT_ORDER:
            employment_label = EMPLOYMENT_LABELS[employment_type]
            section_path = f"{category_path}/{EMPLOYMENT_DIRS[employment_type]}"
            all_count = len(all_buckets[category][employment_type])
            matched_count = len(matched_buckets[category][employment_type])
            lines.append(
                f"| {category_label} | {employment_label} | [{all_count}]({section_path}/all-jobs.md) | "
                f"[{matched_count} matches]({section_path}/matched-for-me.md) |\n"
            )

    featured_label = CATEGORY_LABELS[FEATURED_CATEGORY]
    featured_path = CATEGORY_DIRS[FEATURED_CATEGORY]
    lines.append(f"\n## 🔐 Featured: {featured_label} — Top Full-Time Matches\n\n")
    lines.append(_render_table(matched_buckets[FEATURED_CATEGORY]["full_time"][:15], show_score=True))
    lines.append(
        f"\n[See all {featured_label.lower()} full-time matches]({featured_path}/full-time/matched-for-me.md) "
        f"· [See all jobs found]({featured_path}/full-time/all-jobs.md)\n"
    )

    lines.append(f"\n## 🔐 Featured: {featured_label} — Top Internship Matches\n\n")
    lines.append(_render_table(matched_buckets[FEATURED_CATEGORY]["internship"][:10], show_score=True))
    lines.append(
        f"\n[See all {featured_label.lower()} internship matches]({featured_path}/internships/matched-for-me.md) "
        f"· [See all jobs found]({featured_path}/internships/all-jobs.md)\n"
    )

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


def _remove_stale_flat_category_files(output_dir: Path) -> None:
    """One-time migration cleanup: the board used to have all-jobs.md /
    matched-for-me.md directly under each category directory, before the
    full-time/internship split. Remove them if a prior run left them
    behind, so the repo doesn't carry stale duplicate content forever."""
    for category_dir_name in CATEGORY_DIRS.values():
        for filename in ("all-jobs.md", "matched-for-me.md"):
            stale_path = output_dir / category_dir_name / filename
            if stale_path.exists():
                stale_path.unlink()


def run_export(output_dir: Path, push: bool = True) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _remove_stale_flat_category_files(output_dir)
    all_rows = _load_all_active_jobs()
    matched_rows = _load_matched_jobs()

    all_buckets = _bucket_all_jobs(all_rows)
    matched_buckets = _bucket_matched_jobs(matched_rows)

    for category in CATEGORY_ORDER:
        for employment_type in EMPLOYMENT_ORDER:
            _write_section_pages(
                output_dir, category, employment_type,
                all_buckets[category][employment_type],
                matched_buckets[category][employment_type],
            )
    _write_readme(output_dir, all_buckets, matched_buckets)
    _git_publish(output_dir, push=push)

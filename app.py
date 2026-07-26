"""Roman Job Radar — Streamlit dashboard.

Read-only with respect to applying: every "Apply" action is a link that
opens the company's own application page in a new tab. Nothing in this
file ever submits a form or makes a request on the user's behalf.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import streamlit as st
import yaml

from src.collectors import load_companies
from src.database.models import PipelineRun
from src.database.session import get_session, init_db
from src.resume.profile_builder import load_profile
from src.services.dashboard_data import (
    JobFilters,
    JobRow,
    apply_filters,
    load_visible_jobs,
    page_application_tracker,
    page_biotech_ot,
    page_california,
    page_full_time,
    page_internships,
    page_nationwide,
    page_recommended,
    page_remote,
    page_saved,
    remove_application,
    set_application_notes,
    set_application_status,
)
from src.settings import get_settings

st.set_page_config(page_title="Roman Job Radar", page_icon="\U0001F4E1", layout="wide")

PAGES = [
    "Recommended Jobs",
    "California Jobs",
    "Nationwide Jobs",
    "Remote Jobs",
    "Internships",
    "Full-Time Jobs",
    "Biotech and OT",
    "Saved Jobs",
    "Application Tracker",
    "Settings",
    "Pipeline Health",
]

STATUS_OPTIONS = ["saved", "applied", "interviewing", "rejected", "offer", "archived"]


@st.cache_data(ttl=60)
def load_domain_settings() -> dict:
    with open("config/settings.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_jobs() -> list[JobRow]:
    with get_session() as session:
        return load_visible_jobs(session)


def format_salary(row: JobRow) -> str | None:
    if row.salary_min is None and row.salary_max is None:
        return None
    if row.salary_min is not None and row.salary_max is not None:
        return f"${row.salary_min:,.0f} - ${row.salary_max:,.0f}"
    value = row.salary_min if row.salary_min is not None else row.salary_max
    return f"${value:,.0f}"


def format_posted(row: JobRow) -> str:
    if row.posted_at is None:
        return "Unknown"
    posted = row.posted_at if row.posted_at.tzinfo else row.posted_at.replace(tzinfo=timezone.utc)
    days = (datetime.now(timezone.utc) - posted).days
    if days <= 0:
        return "Today"
    if days == 1:
        return "1 day ago"
    return f"{days} days ago"


def score_color(score: float) -> str:
    if score >= 88:
        return "\U0001F7E2"  # green
    if score >= 80:
        return "\U0001F535"  # blue
    if score >= 70:
        return "\U0001F7E1"  # yellow
    return "⚪"  # white/neutral


def render_job_card(row: JobRow) -> None:
    with st.container(border=True):
        header_col, score_col = st.columns([5, 1])
        with header_col:
            st.markdown(f"### {row.title}")
            meta_bits = [row.company_name]
            if row.location:
                meta_bits.append(row.location)
            if row.workplace_type:
                meta_bits.append(row.workplace_type.title())
            if row.employment_type:
                meta_bits.append(row.employment_type.replace("_", " ").title())
            meta_bits.append(format_posted(row))
            st.caption(" • ".join(meta_bits))
        with score_col:
            st.markdown(f"## {score_color(row.total_score)} {row.total_score:.0f}")

        salary_text = format_salary(row)
        if salary_text:
            # Streamlit's markdown renderer treats a bare "$...$" as inline
            # LaTeX math — without escaping, "$104,000 - $112,320" renders
            # as garbled math notation instead of plain text (caught via
            # live browser testing).
            st.write(f"**Salary:** {salary_text.replace('$', chr(92) + '$')}")

        if row.match_explanation:
            st.write(f"**Why it matches:** {row.match_explanation}")

        skill_col1, skill_col2 = st.columns(2)
        with skill_col1:
            if row.matching_skills:
                st.markdown("**Matching skills**")
                st.markdown(" ".join(f"`{s}`" for s in row.matching_skills))
        with skill_col2:
            missing = row.missing_required_skills + row.missing_preferred_skills
            if missing:
                st.markdown("**Missing skills**")
                labels = [f"`{s}` (required)" for s in row.missing_required_skills] + [
                    f"`{s}` (preferred)" for s in row.missing_preferred_skills
                ]
                st.markdown(" ".join(labels))

        with st.expander("Score breakdown"):
            st.write(
                f"Skills: {row.skills_score:.0f} · Experience: {row.experience_score:.0f} · "
                f"Title: {row.title_score:.0f} · Semantic: {row.semantic_score:.0f} · "
                f"Education: {row.education_score:.0f} · Location: {row.location_score:.0f} · "
                f"Freshness: {row.freshness_score:.0f}"
            )

        action_cols = st.columns([1, 1, 1, 1, 2])
        with action_cols[0]:
            if row.apply_url:
                st.link_button("Apply ↗", row.apply_url, use_container_width=True)
        with action_cols[1]:
            if st.button("Save", key=f"save_{row.job_id}", use_container_width=True):
                with get_session() as session:
                    set_application_status(session, row.job_id, "saved")
                st.rerun()
        with action_cols[2]:
            if st.button("Mark Applied", key=f"applied_{row.job_id}", use_container_width=True):
                with get_session() as session:
                    set_application_status(session, row.job_id, "applied")
                st.rerun()
        with action_cols[3]:
            if st.button("Archive", key=f"archive_{row.job_id}", use_container_width=True):
                with get_session() as session:
                    set_application_status(session, row.job_id, "archived")
                st.rerun()
        with action_cols[4]:
            if row.application_status:
                st.info(f"Status: **{row.application_status}**")

        with st.expander("Notes"):
            note_value = st.text_area(
                "Notes", value=row.application_notes or "", key=f"notes_{row.job_id}", label_visibility="collapsed"
            )
            if st.button("Save note", key=f"save_note_{row.job_id}"):
                with get_session() as session:
                    set_application_notes(session, row.job_id, note_value)
                st.success("Note saved")


def render_sidebar_filters(rows: list[JobRow]) -> JobFilters:
    st.sidebar.header("Filters")

    min_score = st.sidebar.slider("Minimum score", 0, 100, 60, step=5)

    posted_options = {"Any time": None, "Last 7 days": 7, "Last 14 days": 14, "Last 30 days": 30}
    posted_choice = st.sidebar.selectbox("Date posted", list(posted_options.keys()))

    employment_types = sorted({r.employment_type for r in rows if r.employment_type})
    selected_employment = st.sidebar.multiselect("Employment type", employment_types)

    workplace_types = sorted({r.workplace_type for r in rows if r.workplace_type})
    selected_workplace = st.sidebar.multiselect("Workplace type", workplace_types)

    california_only = st.sidebar.checkbox("California only")
    remote_only = st.sidebar.checkbox("Remote only")

    industries = sorted({r.industry for r in rows if r.industry})
    selected_industries = st.sidebar.multiselect("Industry", industries)

    companies = sorted({r.company_name for r in rows})
    selected_companies = st.sidebar.multiselect("Company", companies)

    domain_settings = load_domain_settings()
    role_categories = domain_settings.get("target_role_categories", {})
    category_names = list(role_categories.keys())
    selected_categories = st.sidebar.multiselect("Role category", category_names)
    role_category_titles = None
    if selected_categories:
        titles = []
        for cat in selected_categories:
            titles.extend(role_categories.get(cat, []))
        role_category_titles = tuple(titles)

    max_experience = st.sidebar.slider("Max required experience (years)", 0, 15, 15)
    max_experience_filter = None if max_experience == 15 else float(max_experience)

    min_salary = st.sidebar.number_input("Minimum salary ($)", min_value=0, value=0, step=10000)
    min_salary_filter = None if min_salary == 0 else float(min_salary)

    all_missing_skills = sorted({s for r in rows for s in (r.missing_required_skills + r.missing_preferred_skills)})
    missing_skill = st.sidebar.selectbox("Missing skill", ["Any"] + all_missing_skills)
    missing_skill_filter = None if missing_skill == "Any" else missing_skill

    return JobFilters(
        min_score=float(min_score),
        posted_within_days=posted_options[posted_choice],
        employment_types=tuple(selected_employment) or None,
        workplace_types=tuple(selected_workplace) or None,
        california_only=california_only,
        remote_only=remote_only,
        industries=tuple(selected_industries) or None,
        companies=tuple(selected_companies) or None,
        role_category_titles=role_category_titles,
        max_required_experience=max_experience_filter,
        min_salary=min_salary_filter,
        missing_skill=missing_skill_filter,
    )


def render_job_list_page(rows: list[JobRow], filters: JobFilters, empty_message: str) -> None:
    filtered = apply_filters(rows, filters)
    filtered.sort(key=lambda r: -r.total_score)
    st.caption(f"{len(filtered)} job(s)")
    if not filtered:
        st.info(empty_message)
        return
    for row in filtered:
        render_job_card(row)


def render_application_tracker(rows: list[JobRow]) -> None:
    tracked = page_application_tracker(rows)
    st.caption(f"{len(tracked)} tracked application(s)")
    if not tracked:
        st.info("Save a job from any list page to start tracking it here.")
        return

    for row in tracked:
        with st.container(border=True):
            cols = st.columns([4, 2, 2, 1])
            with cols[0]:
                st.markdown(f"**{row.title}** — {row.company_name}")
                st.caption(row.location or "")
            with cols[1]:
                new_status = st.selectbox(
                    "Status",
                    STATUS_OPTIONS,
                    index=STATUS_OPTIONS.index(row.application_status)
                    if row.application_status in STATUS_OPTIONS
                    else 0,
                    key=f"tracker_status_{row.job_id}",
                    label_visibility="collapsed",
                )
                if new_status != row.application_status:
                    with get_session() as session:
                        set_application_status(session, row.job_id, new_status)
                    st.rerun()
            with cols[2]:
                if row.apply_url:
                    st.link_button("Open posting ↗", row.apply_url, use_container_width=True)
            with cols[3]:
                if st.button("Remove", key=f"remove_{row.job_id}", use_container_width=True):
                    with get_session() as session:
                        remove_application(session, row.job_id)
                    st.rerun()
            note_value = st.text_area(
                "Notes", value=row.application_notes or "", key=f"tracker_notes_{row.job_id}"
            )
            if st.button("Save note", key=f"tracker_save_note_{row.job_id}"):
                with get_session() as session:
                    set_application_notes(session, row.job_id, note_value)
                st.success("Note saved")


def render_settings_page() -> None:
    profile = load_profile()
    app_settings = get_settings()
    domain_settings = load_domain_settings()
    companies = load_companies()

    st.subheader("Candidate profile")
    st.write(f"**Name:** {profile.candidate.get('name')}")
    st.write(f"**Target role categories:** {', '.join(profile.target_role_categories)}")
    st.write(f"**Skills tracked:** {len(profile.all_skills())}")
    with st.expander("Full skill list"):
        st.write(", ".join(profile.all_skills()))

    st.divider()
    st.subheader("Alerting & pipeline thresholds (.env)")
    col1, col2, col3 = st.columns(3)
    col1.metric("Notifications enabled", "Yes" if app_settings.enable_notifications else "No")
    col2.metric("Match alert threshold", app_settings.match_alert_threshold)
    col3.metric("Immediate alert threshold", app_settings.immediate_alert_threshold)
    st.caption(f"Search lookback window: {app_settings.search_lookback_days} days")
    st.write(
        f"**Pushover configured:** {'Yes' if app_settings.has_pushover_configured() else 'No'}  \n"
        f"**Email configured:** {'Yes' if app_settings.has_email_configured() else 'No'}"
        + (f" (to {app_settings.email_to})" if app_settings.has_email_configured() else "")
    )
    if not app_settings.enable_notifications:
        st.info(
            "Notifications are disabled. Send a test below to confirm your Pushover and/or email "
            "credentials work, then set ENABLE_NOTIFICATIONS=true in .env to start receiving real "
            "alerts from the pipeline."
        )

    st.caption(
        "Sends a real test notification to every configured channel (Pushover and/or email), "
        "regardless of ENABLE_NOTIFICATIONS."
    )
    if st.button("Send test notification"):
        from src.alerts import send_test_notification

        if not app_settings.has_pushover_configured() and not app_settings.has_email_configured():
            st.error("Set up Pushover (PUSHOVER_USER_KEY/PUSHOVER_APP_TOKEN) and/or email (SMTP_*/EMAIL_TO) in .env first.")
        else:
            with st.spinner("Sending..."):
                results = send_test_notification(app_settings)
            for channel, success in results.items():
                if success:
                    st.success(f"Test notification sent via {channel} — check it arrived.")
                else:
                    st.error(f"Failed to send via {channel}. Double-check its credentials.")

    st.divider()
    st.subheader("Company registry")
    by_ats: dict[str, int] = {}
    for c in companies:
        by_ats[c.ats_type] = by_ats.get(c.ats_type, 0) + 1
    st.write(f"**{len(companies)} active companies** — " + ", ".join(f"{k}: {v}" for k, v in sorted(by_ats.items())))

    st.divider()
    st.subheader("Refresh data now")
    st.caption("Runs the collection pipeline against a chosen subset of companies. Larger selections take longer.")
    company_names = [c.name for c in companies]
    chosen = st.multiselect("Companies to refresh", company_names, default=company_names[:5])
    limit = st.number_input("Max jobs per company (for a quick refresh)", min_value=1, value=25, step=5)
    if st.button("Run pipeline now"):
        from src.services.pipeline import run_pipeline

        with st.spinner(f"Collecting from {len(chosen)} companies..."):
            stats = run_pipeline(company_names=chosen, jobs_per_company_limit=int(limit))
        st.success(
            f"Processed {stats.companies_processed} companies "
            f"({stats.companies_failed} failed) — {stats.new_jobs} new jobs, "
            f"{stats.scored_jobs} scored."
        )
        if stats.errors:
            with st.expander("Errors"):
                for err in stats.errors:
                    st.write(f"- {err}")
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.subheader("Delete all data")
    st.caption(
        "Permanently deletes every collected job, match, application, and pipeline run from the local "
        "database. Does not touch your profile, resume, or company config — only data the pipeline "
        "itself collected. This cannot be undone."
    )
    confirm_delete = st.checkbox("I understand this permanently deletes all collected data")
    if st.button("Delete all data", disabled=not confirm_delete, type="primary"):
        from src.database.session import delete_all_data

        counts = delete_all_data()
        st.success(f"Deleted: {counts}")
        st.cache_data.clear()
        st.rerun()


def render_pipeline_health_page() -> None:
    with get_session() as session:
        runs = session.query(PipelineRun).order_by(PipelineRun.started_at.desc()).limit(50).all()
        run_data = [
            {
                "started_at": r.started_at,
                "completed_at": r.completed_at,
                "source": r.source or "all",
                "jobs_found": r.jobs_found,
                "new_jobs": r.new_jobs,
                "errors": json.loads(r.errors) if r.errors else [],
            }
            for r in runs
        ]

    if not run_data:
        st.info("No pipeline runs recorded yet. Use Settings → Refresh data now to run one.")
        return

    latest = run_data[0]
    col1, col2, col3 = st.columns(3)
    col1.metric("Last run", latest["started_at"].strftime("%Y-%m-%d %H:%M") if latest["started_at"] else "—")
    col2.metric("Jobs found", latest["jobs_found"])
    col3.metric("New jobs", latest["new_jobs"])

    recent_failures = sum(1 for r in run_data[:5] if r["errors"])
    if recent_failures >= 3:
        st.error(f"{recent_failures} of the last 5 runs had errors — check company configuration.")

    st.divider()
    st.subheader("Run history")
    for r in run_data:
        with st.container(border=True):
            st.write(
                f"**{r['started_at'].strftime('%Y-%m-%d %H:%M') if r['started_at'] else '—'}** "
                f"({r['source']}) — {r['jobs_found']} found, {r['new_jobs']} new"
            )
            if r["errors"]:
                with st.expander(f"{len(r['errors'])} error(s)"):
                    for err in r["errors"]:
                        st.write(f"- {err}")


def main() -> None:
    init_db()

    st.sidebar.title("\U0001F4E1 Roman Job Radar")
    page = st.sidebar.radio("Navigate", PAGES, label_visibility="collapsed")
    st.sidebar.divider()

    if page in ("Settings", "Pipeline Health"):
        st.title(page)
        if page == "Settings":
            render_settings_page()
        else:
            render_pipeline_health_page()
        return

    rows = load_jobs()
    filters = render_sidebar_filters(rows)

    st.title(page)

    if page == "Recommended Jobs":
        base = page_recommended(rows, min_score=max(filters.min_score, 80.0))
        render_job_list_page(base, JobFilters(**{**filters.__dict__, "min_score": 0.0}), "No strong matches yet — try lowering filters or refreshing data in Settings.")
    elif page == "California Jobs":
        render_job_list_page(page_california(rows), filters, "No California jobs match your filters.")
    elif page == "Nationwide Jobs":
        render_job_list_page(page_nationwide(rows), filters, "No jobs match your filters.")
    elif page == "Remote Jobs":
        render_job_list_page(page_remote(rows), filters, "No remote jobs match your filters.")
    elif page == "Internships":
        render_job_list_page(page_internships(rows), filters, "No internships match your filters.")
    elif page == "Full-Time Jobs":
        render_job_list_page(page_full_time(rows), filters, "No full-time jobs match your filters.")
    elif page == "Biotech and OT":
        render_job_list_page(page_biotech_ot(rows), filters, "No biotech/OT jobs match your filters.")
    elif page == "Saved Jobs":
        render_job_list_page(page_saved(rows), JobFilters(**{**filters.__dict__, "min_score": 0.0}), "Nothing saved yet.")
    elif page == "Application Tracker":
        render_application_tracker(rows)


if __name__ == "__main__":
    main()

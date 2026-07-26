"""Job search pipeline CLI entry point.

Usage:
    python run_pipeline.py
    python run_pipeline.py --source greenhouse
    python run_pipeline.py --company "Genentech"
    python run_pipeline.py --company "Genentech" --company "Lonza"
    python run_pipeline.py --dry-run
    python run_pipeline.py --send-test-alert
    python run_pipeline.py --limit 25          # dev/demo: cap jobs per company
    python run_pipeline.py --extract-resume-text   # (re)generate data/resume_raw.txt from resume.pdf
    python run_pipeline.py --delete-all-data       # wipe all collected jobs/matches/applications/history

Exit code is 1 only on a catastrophic failure (every requested company
failed to collect); individual company failures are isolated by design
(see src/collectors/base.py) and don't fail the run. Three consecutive
zero-progress runs additionally trigger a phone alert on their own — see
src/services/pipeline.py's check_repeated_failures.
"""
from __future__ import annotations

import argparse
import sys

from src.database.session import init_db
from src.logging_config import configure_logging, get_logger
from src.settings import get_settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Roman Job Radar collection/scoring pipeline.")
    parser.add_argument(
        "--source",
        help="Only run collectors for this ATS type (greenhouse, lever, ashby, workday, government, custom)",
    )
    parser.add_argument(
        "--company", action="append", dest="companies", metavar="NAME",
        help="Only run for this company (repeatable: --company A --company B)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Collect and evaluate eligibility but do not write to the database or send alerts",
    )
    parser.add_argument(
        "--send-test-alert", action="store_true",
        help="Send a test Pushover notification (using .env credentials) and exit, without running the pipeline",
    )
    parser.add_argument(
        "--limit", type=int, default=None, metavar="N",
        help="Cap jobs processed per company (development/demo use — disables missing-job expiration)",
    )
    parser.add_argument(
        "--extract-resume-text", action="store_true",
        help="(Re)extract text from resume.pdf into data/resume_raw.txt and exit, without running the pipeline",
    )
    parser.add_argument(
        "--delete-all-data", action="store_true",
        help="Permanently delete all collected jobs, matches, applications, and pipeline history. "
        "Does not touch config/profile.yaml, companies.yaml, or resume.pdf. Prompts for confirmation "
        "unless --yes is also given.",
    )
    parser.add_argument(
        "--yes", action="store_true",
        help="Skip the confirmation prompt for --delete-all-data (for scripted/non-interactive use)",
    )
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)
    logger = get_logger("run_pipeline")

    if args.extract_resume_text:
        from pathlib import Path

        from src.resume.parser import extract_text, save_raw_text

        resume_path = Path("resume.pdf")
        if not resume_path.exists():
            logger.error("resume.pdf not found at the project root.")
            return 1
        text = extract_text(resume_path)
        save_raw_text(text, Path("data/resume_raw.txt"))
        logger.info("Extracted resume text saved to data/resume_raw.txt.")
        return 0

    if args.delete_all_data:
        from src.database.session import delete_all_data

        if not args.yes:
            confirm = input(
                "This will PERMANENTLY delete all collected jobs, matches, applications, and pipeline "
                "history from the local database. Your profile, resume, and company config are not "
                "affected. Type 'yes' to confirm: "
            )
            if confirm.strip().lower() != "yes":
                logger.info("Cancelled — no data was deleted.")
                return 0
        init_db()
        counts = delete_all_data()
        logger.info("Deleted all data: %s", counts)
        return 0

    if args.send_test_alert:
        from src.alerts import send_test_notification

        if not settings.has_pushover_configured() and not settings.has_email_configured():
            logger.error(
                "No notification channel configured. Set PUSHOVER_USER_KEY+PUSHOVER_APP_TOKEN "
                "and/or SMTP_HOST+SMTP_USERNAME+SMTP_PASSWORD+EMAIL_TO in .env before sending a test alert."
            )
            return 1
        results = send_test_notification(settings)
        for channel, success in results.items():
            if success:
                logger.info("Test notification sent via %s — check it arrived.", channel)
            else:
                logger.error("Failed to send test notification via %s. Double-check its credentials.", channel)
        return 0 if all(results.values()) else 1

    init_db()

    from src.services.pipeline import run_pipeline

    logger.info(
        "Starting pipeline run (source=%s, companies=%s, dry_run=%s, limit=%s)",
        args.source, args.companies, args.dry_run, args.limit,
    )
    stats = run_pipeline(
        company_names=args.companies,
        source_filter=args.source,
        dry_run=args.dry_run,
        jobs_per_company_limit=args.limit,
    )

    total_companies = stats.companies_processed + stats.companies_failed
    logger.info(
        "Pipeline finished: %d/%d companies processed, %d jobs found, %d new, %d updated, "
        "%d expired, %d eligible, %d scored",
        stats.companies_processed, total_companies, stats.jobs_found, stats.new_jobs,
        stats.updated_jobs, stats.expired_jobs, stats.eligible_jobs, stats.scored_jobs,
    )
    if stats.alert_stats:
        logger.info(
            "Alerts: %d immediate, digest_sent=%s (%d jobs), %d failure(s)",
            stats.alert_stats.immediate_sent, stats.alert_stats.digest_sent,
            stats.alert_stats.digest_count, stats.alert_stats.failures,
        )
    if stats.repeated_failure_alert_sent:
        logger.warning("Sent a repeated-failure alert — the pipeline has made no progress for several runs.")
    if stats.errors:
        logger.warning("Errors during this run:\n%s", "\n".join(stats.errors))

    if total_companies > 0 and stats.companies_processed == 0:
        logger.error("Every requested company failed to collect — treating this run as failed.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

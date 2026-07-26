"""Alert orchestration: picks the right provider, formats notifications,
and routes newly-found jobs to an immediate alert or the digest based on
score.

Duplicate prevention is deliberately NOT implemented as a new "alerted"
flag or table here — the jobs table (fixed in Phase 3) has no such column.
Instead it falls out of how the caller uses this module: src/services/
pipeline.py only ever passes JOBS IT JUST CREATED (Job rows that didn't
exist before this run) into send_job_alerts(). A job that already existed
gets its last_seen_at bumped, not a new row, so it's never handed to this
module again — the same real-world posting can't generate two alerts
across separate pipeline runs.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.alerts.base import AlertProvider, ConsoleProvider, MultiProvider, Notification, format_job_notification
from src.alerts.digest import DigestEntry, build_digest_notification
from src.alerts.email_provider import EmailProvider
from src.alerts.pushover import PushoverProvider
from src.logging_config import get_logger
from src.settings import Settings

logger = get_logger(__name__)


@dataclass
class ScoredJobForAlert:
    score: float
    title: str
    company: str
    location: str | None
    apply_url: str | None
    match_explanation: str | None


@dataclass
class AlertStats:
    immediate_sent: int = 0
    digest_sent: bool = False
    digest_count: int = 0
    failures: int = 0
    errors: list[str] = field(default_factory=list)


def _build_email_provider(settings: Settings) -> EmailProvider:
    return EmailProvider(
        smtp_host=settings.smtp_host,
        smtp_username=settings.smtp_username,
        smtp_password=settings.smtp_password,
        email_to=settings.email_to,
        smtp_port=settings.smtp_port,
        email_from=settings.email_from,
    )


def get_alert_provider(settings: Settings) -> AlertProvider:
    """The single gate deciding whether real alerts go out.
    ENABLE_NOTIFICATIONS=false (the default) always uses the console
    provider. When enabled, fans out to whichever of Pushover/email are
    fully configured — Settings already refuses to start if neither is
    (see src/settings.py's validate_for_notifications)."""
    if not settings.enable_notifications:
        return ConsoleProvider()

    providers: list[AlertProvider] = []
    if settings.has_pushover_configured():
        providers.append(PushoverProvider(settings.pushover_user_key, settings.pushover_app_token))
    if settings.has_email_configured():
        providers.append(_build_email_provider(settings))

    if not providers:
        return ConsoleProvider()  # shouldn't happen given validate_for_notifications, but stay safe
    if len(providers) == 1:
        return providers[0]
    return MultiProvider(providers)


def send_test_notification(settings: Settings) -> dict[str, bool]:
    """Sends a real test notification to every configured channel,
    regardless of ENABLE_NOTIFICATIONS — this is the manual check meant to
    happen *before* flipping that flag on. Returns per-channel results
    (e.g. {"pushover": True, "email": False}). Raises ValueError if nothing
    is configured at all."""
    if not settings.has_pushover_configured() and not settings.has_email_configured():
        raise ValueError("No notification channel is configured — set up Pushover and/or email credentials first")

    notification = Notification(
        title="Roman Job Radar — Test Notification",
        message="If you're seeing this, your notification setup is configured correctly.",
    )
    results: dict[str, bool] = {}
    if settings.has_pushover_configured():
        provider = PushoverProvider(settings.pushover_user_key, settings.pushover_app_token)
        results["pushover"] = provider.send(notification)
    if settings.has_email_configured():
        results["email"] = _build_email_provider(settings).send(notification)
    return results


def send_job_alerts(
    scored_new_jobs: list[ScoredJobForAlert],
    match_alert_threshold: int,
    immediate_alert_threshold: int,
    provider: AlertProvider,
) -> AlertStats:
    stats = AlertStats()
    digest_entries: list[DigestEntry] = []

    for job in scored_new_jobs:
        if job.score >= immediate_alert_threshold:
            notification = format_job_notification(
                job.score, job.title, job.company, job.location, job.match_explanation, job.apply_url
            )
            if provider.send(notification):
                stats.immediate_sent += 1
            else:
                stats.failures += 1
                stats.errors.append(f"Failed to send immediate alert for {job.title} at {job.company}")
        elif job.score >= match_alert_threshold:
            digest_entries.append(
                DigestEntry(score=job.score, title=job.title, company=job.company, location=job.location, apply_url=job.apply_url)
            )

    if digest_entries:
        notification = build_digest_notification(digest_entries)
        if notification and provider.send(notification):
            stats.digest_sent = True
            stats.digest_count = len(digest_entries)
        elif notification:
            stats.failures += 1
            stats.errors.append("Failed to send digest notification")

    return stats


__all__ = [
    "AlertProvider",
    "ConsoleProvider",
    "MultiProvider",
    "PushoverProvider",
    "EmailProvider",
    "Notification",
    "DigestEntry",
    "ScoredJobForAlert",
    "AlertStats",
    "get_alert_provider",
    "send_test_notification",
    "send_job_alerts",
    "build_digest_notification",
    "format_job_notification",
]

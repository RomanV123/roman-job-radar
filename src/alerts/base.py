"""Shared interface for phone-alert providers.

Notifications must stay disabled until valid Pushover credentials exist,
a test notification has been sent successfully, and ENABLE_NOTIFICATIONS is
explicitly set to true (see src/alerts/__init__.py's get_alert_provider,
which is the single place that decides Console vs Pushover).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from src.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class Notification:
    title: str
    message: str
    url: str | None = None
    url_title: str | None = None


class AlertProvider(ABC):
    @abstractmethod
    def send(self, notification: Notification) -> bool:
        """Returns True if the notification was delivered (or, for the
        console provider, logged) successfully."""
        raise NotImplementedError


class ConsoleProvider(AlertProvider):
    """Prints notifications to the log instead of sending them. Used
    whenever ENABLE_NOTIFICATIONS is false, and always available directly
    for local development/testing without touching a real phone."""

    def send(self, notification: Notification) -> bool:
        lines = [f"Title: {notification.title}", f"Message: {notification.message}"]
        if notification.url:
            lines.append(f"URL: {notification.url}")
        logger.info("=== NOTIFICATION (console) ===\n%s", "\n".join(lines))
        return True


class MultiProvider(AlertProvider):
    """Fans a notification out to several providers at once — e.g.
    Pushover AND email simultaneously, when both are configured. Succeeds
    if at least one channel delivers; logs (but doesn't raise on) the rest."""

    def __init__(self, providers: list[AlertProvider]):
        self.providers = providers

    def send(self, notification: Notification) -> bool:
        results = []
        for provider in self.providers:
            try:
                results.append(provider.send(notification))
            except Exception:  # noqa: BLE001 - one channel failing shouldn't break the others
                logger.exception("Notification provider %s raised while sending", type(provider).__name__)
                results.append(False)
        return any(results)


def format_job_notification(
    score: float,
    title: str,
    company: str,
    location: str | None,
    match_explanation: str | None,
    apply_url: str | None,
) -> Notification:
    """Builds the per-job alert exactly matching the spec's example:

        Title:   92% Match — OT Cybersecurity Analyst
        Message: Genentech | South San Francisco, CA
                 Matches your OT infrastructure, Python, Splunk...
                 Apply: [direct URL]

    The apply URL is included both as message text (works everywhere,
    including the console provider and any client that ignores rich
    fields) and as Notification.url (renders as a tappable button in the
    Pushover app).
    """
    notification_title = f"{score:.0f}% Match — {title}"
    lines = [f"{company} | {location or 'Location unknown'}"]
    if match_explanation:
        lines.append(match_explanation)
    if apply_url:
        lines.append(f"Apply: {apply_url}")
    return Notification(
        title=notification_title,
        message="\n".join(lines),
        url=apply_url,
        url_title="Apply" if apply_url else None,
    )

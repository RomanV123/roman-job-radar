"""Batches jobs that cross MATCH_ALERT_THRESHOLD (but not
IMMEDIATE_ALERT_THRESHOLD) into a single combined notification, so a run
that finds several solid-but-not-exceptional matches doesn't send one
alert per job.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.alerts.base import Notification

MAX_DIGEST_ENTRIES_SHOWN = 10


@dataclass
class DigestEntry:
    score: float
    title: str
    company: str
    location: str | None
    apply_url: str | None


def build_digest_notification(entries: list[DigestEntry]) -> Notification | None:
    if not entries:
        return None

    ranked = sorted(entries, key=lambda e: -e.score)
    plural = "es" if len(ranked) != 1 else ""
    title = f"{len(ranked)} new strong match{plural}"

    lines = [
        f"{e.score:.0f}% — {e.title} at {e.company} ({e.location or 'Location unknown'})"
        for e in ranked[:MAX_DIGEST_ENTRIES_SHOWN]
    ]
    remaining = len(ranked) - MAX_DIGEST_ENTRIES_SHOWN
    if remaining > 0:
        lines.append(f"...and {remaining} more in the dashboard")

    return Notification(title=title, message="\n".join(lines))

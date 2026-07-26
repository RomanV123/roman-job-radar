"""Email notification provider via SMTP — a second, independent channel
alongside Pushover (src/alerts/pushover.py). Uses only the standard
library (smtplib/email), no new dependency needed.

Most providers (Gmail, Outlook/Hotmail, etc.) require an "app password"
for SMTP login when 2FA is enabled on the sending account — not your
regular account password. Generate one from the sending account's
security settings and use that as SMTP_PASSWORD.
"""
from __future__ import annotations

import smtplib
from email.mime.text import MIMEText

from src.alerts.base import AlertProvider, Notification
from src.logging_config import get_logger

logger = get_logger(__name__)


class EmailProvider(AlertProvider):
    def __init__(
        self,
        smtp_host: str,
        smtp_username: str,
        smtp_password: str,
        email_to: str,
        smtp_port: int = 587,
        email_from: str = "",
        timeout: float = 10.0,
    ):
        if not smtp_host or not smtp_username or not smtp_password or not email_to:
            raise ValueError("Email provider requires smtp_host, smtp_username, smtp_password, and email_to")
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_username = smtp_username
        self.smtp_password = smtp_password
        self.email_from = email_from or smtp_username
        self.email_to = email_to
        self.timeout = timeout

    def send(self, notification: Notification) -> bool:
        message = MIMEText(notification.message)
        message["Subject"] = notification.title
        message["From"] = self.email_from
        message["To"] = self.email_to

        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=self.timeout) as server:
                server.starttls()
                server.login(self.smtp_username, self.smtp_password)
                server.sendmail(self.email_from, [self.email_to], message.as_string())
            return True
        except (smtplib.SMTPException, OSError) as exc:
            logger.error("Failed to send email notification: %s", exc)
            return False

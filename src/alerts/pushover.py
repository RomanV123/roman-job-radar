"""Pushover phone-notification provider.

Public API docs: https://pushover.net/api
    POST https://api.pushover.net/1/messages.json
    form fields: token (app token), user (user key), title, message, url, url_title
"""
from __future__ import annotations

import httpx

from src.alerts.base import AlertProvider, Notification
from src.logging_config import get_logger

logger = get_logger(__name__)

PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"


class PushoverProvider(AlertProvider):
    def __init__(self, user_key: str, app_token: str, timeout: float = 10.0):
        if not user_key or not app_token:
            raise ValueError("Pushover requires both a user_key and an app_token")
        self.user_key = user_key
        self.app_token = app_token
        self.timeout = timeout

    def send(self, notification: Notification) -> bool:
        payload = {
            "token": self.app_token,
            "user": self.user_key,
            "title": notification.title,
            "message": notification.message,
        }
        if notification.url:
            payload["url"] = notification.url
            payload["url_title"] = notification.url_title or "Apply"

        try:
            response = httpx.post(PUSHOVER_API_URL, data=payload, timeout=self.timeout)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("Failed to reach Pushover: %s", exc)
            return False

        data = response.json()
        if data.get("status") != 1:
            logger.error("Pushover rejected the notification: %s", data)
            return False
        return True

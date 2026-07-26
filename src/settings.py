"""Application-wide runtime settings, loaded from environment variables.

Distinct from config/*.yaml, which holds user-editable domain data
(profile, scoring weights, companies, skills) rather than secrets/runtime knobs.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    pushover_user_key: str = Field(default="", alias="PUSHOVER_USER_KEY")
    pushover_app_token: str = Field(default="", alias="PUSHOVER_APP_TOKEN")
    enable_notifications: bool = Field(default=False, alias="ENABLE_NOTIFICATIONS")

    # Email is a second, independent notification channel — both Pushover
    # and email can be active at once (see src/alerts/__init__.py's
    # get_alert_provider, which fans out to whichever are configured).
    smtp_host: str = Field(default="", alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_username: str = Field(default="", alias="SMTP_USERNAME")
    smtp_password: str = Field(default="", alias="SMTP_PASSWORD")
    email_from: str = Field(default="", alias="EMAIL_FROM")
    email_to: str = Field(default="", alias="EMAIL_TO")

    database_url: str = Field(default="sqlite:///data/job_radar.db", alias="DATABASE_URL")

    match_alert_threshold: int = Field(default=80, alias="MATCH_ALERT_THRESHOLD")
    immediate_alert_threshold: int = Field(default=88, alias="IMMEDIATE_ALERT_THRESHOLD")
    search_lookback_days: int = Field(default=30, alias="SEARCH_LOOKBACK_DAYS")

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @field_validator("match_alert_threshold", "immediate_alert_threshold")
    @classmethod
    def _score_in_range(cls, v: int) -> int:
        if not 0 <= v <= 100:
            raise ValueError("threshold must be between 0 and 100")
        return v

    @field_validator("search_lookback_days")
    @classmethod
    def _positive_lookback(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("SEARCH_LOOKBACK_DAYS must be positive")
        return v

    def has_pushover_configured(self) -> bool:
        return bool(self.pushover_user_key and self.pushover_app_token)

    def has_email_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_username and self.smtp_password and self.email_to)

    def validate_for_notifications(self) -> None:
        """Raise if notifications are enabled but no channel is fully
        configured. Either Pushover or email (or both) is enough — you
        don't need to set up both."""
        if self.enable_notifications and not (self.has_pushover_configured() or self.has_email_configured()):
            raise ValueError(
                "ENABLE_NOTIFICATIONS=true requires either PUSHOVER_USER_KEY+PUSHOVER_APP_TOKEN "
                "or SMTP_HOST+SMTP_USERNAME+SMTP_PASSWORD+EMAIL_TO to be set"
            )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_for_notifications()
    return settings

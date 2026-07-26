import pytest

from src.settings import Settings


def test_defaults_load_without_env(monkeypatch):
    monkeypatch.delenv("PUSHOVER_USER_KEY", raising=False)
    monkeypatch.delenv("PUSHOVER_APP_TOKEN", raising=False)
    monkeypatch.delenv("ENABLE_NOTIFICATIONS", raising=False)
    settings = Settings(_env_file=None)
    assert settings.enable_notifications is False
    assert settings.database_url == "sqlite:///data/job_radar.db"
    assert settings.match_alert_threshold == 80
    assert settings.immediate_alert_threshold == 88
    assert settings.search_lookback_days == 30


def test_notifications_require_credentials():
    settings = Settings(_env_file=None, ENABLE_NOTIFICATIONS=True)
    with pytest.raises(ValueError, match="requires either PUSHOVER_USER_KEY"):
        settings.validate_for_notifications()


def test_notifications_ok_with_pushover_credentials():
    settings = Settings(
        _env_file=None,
        ENABLE_NOTIFICATIONS=True,
        PUSHOVER_USER_KEY="abc",
        PUSHOVER_APP_TOKEN="def",
    )
    settings.validate_for_notifications()  # should not raise


def test_notifications_ok_with_email_only():
    settings = Settings(
        _env_file=None,
        ENABLE_NOTIFICATIONS=True,
        SMTP_HOST="smtp.example.com",
        SMTP_USERNAME="me@example.com",
        SMTP_PASSWORD="pw",
        EMAIL_TO="romanvasilyev@hotmail.com",
    )
    settings.validate_for_notifications()  # should not raise


def test_has_pushover_configured():
    assert Settings(_env_file=None, PUSHOVER_USER_KEY="u", PUSHOVER_APP_TOKEN="t").has_pushover_configured() is True
    assert Settings(_env_file=None, PUSHOVER_USER_KEY="u").has_pushover_configured() is False
    assert Settings(_env_file=None).has_pushover_configured() is False


def test_has_email_configured():
    full = Settings(
        _env_file=None, SMTP_HOST="h", SMTP_USERNAME="u", SMTP_PASSWORD="p", EMAIL_TO="a@b.com"
    )
    assert full.has_email_configured() is True
    partial = Settings(_env_file=None, SMTP_HOST="h", SMTP_USERNAME="u")
    assert partial.has_email_configured() is False
    assert Settings(_env_file=None).has_email_configured() is False


def test_email_port_defaults_to_587():
    assert Settings(_env_file=None).smtp_port == 587


@pytest.mark.parametrize("value", [-1, 101])
def test_threshold_out_of_range_rejected(value):
    with pytest.raises(ValueError):
        Settings(_env_file=None, MATCH_ALERT_THRESHOLD=value)


def test_negative_lookback_rejected():
    with pytest.raises(ValueError):
        Settings(_env_file=None, SEARCH_LOOKBACK_DAYS=0)

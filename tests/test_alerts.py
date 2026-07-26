import httpx
import pytest
import respx

from src.alerts import (
    AlertStats,
    ScoredJobForAlert,
    get_alert_provider,
    send_job_alerts,
    send_test_notification,
)
from src.alerts.base import ConsoleProvider, MultiProvider, Notification, format_job_notification
from src.alerts.digest import DigestEntry, build_digest_notification
from src.alerts.email_provider import EmailProvider
from src.alerts.pushover import PUSHOVER_API_URL, PushoverProvider
from src.settings import Settings


def make_settings(**overrides):
    defaults = dict(_env_file=None)
    defaults.update(overrides)
    return Settings(**defaults)


# ---------- ConsoleProvider ----------

def test_console_provider_always_succeeds(caplog):
    provider = ConsoleProvider()
    result = provider.send(Notification(title="Test", message="Body"))
    assert result is True


# ---------- format_job_notification ----------

def test_format_job_notification_matches_spec_example():
    notification = format_job_notification(
        score=92,
        title="OT Cybersecurity Analyst",
        company="Genentech",
        location="South San Francisco, CA",
        match_explanation="Matches your OT infrastructure, Python, Splunk, network security, and biotech experience.",
        apply_url="https://example.com/apply/1",
    )
    assert notification.title == "92% Match — OT Cybersecurity Analyst"
    assert "Genentech | South San Francisco, CA" in notification.message
    assert "Matches your OT infrastructure" in notification.message
    assert "Apply: https://example.com/apply/1" in notification.message
    assert notification.url == "https://example.com/apply/1"
    assert notification.url_title == "Apply"


def test_format_job_notification_handles_missing_location_and_url():
    notification = format_job_notification(
        score=75, title="Analyst", company="Acme", location=None, match_explanation=None, apply_url=None
    )
    assert "Location unknown" in notification.message
    assert notification.url is None


# ---------- PushoverProvider ----------

def test_pushover_provider_requires_credentials():
    with pytest.raises(ValueError):
        PushoverProvider("", "")
    with pytest.raises(ValueError):
        PushoverProvider("user", "")


@respx.mock
def test_pushover_provider_send_success():
    respx.post(PUSHOVER_API_URL).mock(return_value=httpx.Response(200, json={"status": 1}))
    provider = PushoverProvider("user_key", "app_token")
    result = provider.send(Notification(title="Test", message="Body", url="https://example.com"))
    assert result is True


@respx.mock
def test_pushover_provider_sends_expected_payload():
    route = respx.post(PUSHOVER_API_URL).mock(return_value=httpx.Response(200, json={"status": 1}))
    provider = PushoverProvider("user_key", "app_token")
    provider.send(Notification(title="Test Title", message="Test Message", url="https://x.com/1", url_title="Apply"))
    request = route.calls[0].request
    body = request.content.decode()
    assert "token=app_token" in body
    assert "user=user_key" in body
    assert "Test+Title" in body or "Test%20Title" in body


@respx.mock
def test_pushover_provider_rejected_by_api_returns_false():
    respx.post(PUSHOVER_API_URL).mock(return_value=httpx.Response(200, json={"status": 0, "errors": ["invalid token"]}))
    provider = PushoverProvider("user_key", "bad_token")
    result = provider.send(Notification(title="Test", message="Body"))
    assert result is False


@respx.mock
def test_pushover_provider_http_error_returns_false():
    respx.post(PUSHOVER_API_URL).mock(return_value=httpx.Response(500))
    provider = PushoverProvider("user_key", "app_token")
    result = provider.send(Notification(title="Test", message="Body"))
    assert result is False


@respx.mock
def test_pushover_provider_network_error_returns_false():
    respx.post(PUSHOVER_API_URL).mock(side_effect=httpx.ConnectError("no network"))
    provider = PushoverProvider("user_key", "app_token")
    result = provider.send(Notification(title="Test", message="Body"))
    assert result is False


# ---------- digest ----------

def test_build_digest_notification_empty_returns_none():
    assert build_digest_notification([]) is None


def test_build_digest_notification_sorts_by_score_descending():
    entries = [
        DigestEntry(score=65, title="Analyst A", company="Acme", location="TX", apply_url=None),
        DigestEntry(score=79, title="Analyst B", company="Beta", location="NY", apply_url=None),
    ]
    notification = build_digest_notification(entries)
    lines = notification.message.split("\n")
    assert lines[0].startswith("79%")
    assert lines[1].startswith("65%")


def test_build_digest_notification_title_counts_entries():
    entries = [DigestEntry(score=70, title="A", company="Acme", location=None, apply_url=None)]
    notification = build_digest_notification(entries)
    assert notification.title == "1 new strong match"

    entries.append(DigestEntry(score=71, title="B", company="Beta", location=None, apply_url=None))
    notification2 = build_digest_notification(entries)
    assert notification2.title == "2 new strong matches"


def test_build_digest_notification_caps_shown_entries():
    entries = [
        DigestEntry(score=60 + i, title=f"Job {i}", company="Acme", location=None, apply_url=None) for i in range(15)
    ]
    notification = build_digest_notification(entries)
    assert "...and 5 more in the dashboard" in notification.message


# ---------- EmailProvider ----------

def test_email_provider_requires_all_fields():
    with pytest.raises(ValueError):
        EmailProvider(smtp_host="", smtp_username="u", smtp_password="p", email_to="a@b.com")
    with pytest.raises(ValueError):
        EmailProvider(smtp_host="smtp.example.com", smtp_username="u", smtp_password="p", email_to="")


def test_email_provider_send_success(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=10.0):
            sent["host"] = host
            sent["port"] = port

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            sent["starttls"] = True

        def login(self, username, password):
            sent["login"] = (username, password)

        def sendmail(self, from_addr, to_addrs, message):
            sent["from"] = from_addr
            sent["to"] = to_addrs
            sent["message"] = message

    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    provider = EmailProvider(
        smtp_host="smtp.example.com", smtp_username="me@example.com", smtp_password="pw",
        email_to="romanvasilyev@hotmail.com", smtp_port=587,
    )
    result = provider.send(Notification(title="Test", message="Body"))
    assert result is True
    assert sent["host"] == "smtp.example.com"
    assert sent["login"] == ("me@example.com", "pw")
    assert sent["to"] == ["romanvasilyev@hotmail.com"]
    assert "Test" in sent["message"]


def test_email_provider_send_failure_returns_false(monkeypatch):
    import smtplib

    class FailingSMTP:
        def __init__(self, host, port, timeout=10.0):
            pass

        def __enter__(self):
            raise smtplib.SMTPException("auth failed")

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr("smtplib.SMTP", FailingSMTP)
    provider = EmailProvider(
        smtp_host="smtp.example.com", smtp_username="me@example.com", smtp_password="wrong",
        email_to="romanvasilyev@hotmail.com",
    )
    assert provider.send(Notification(title="Test", message="Body")) is False


def test_email_provider_defaults_from_to_username():
    provider = EmailProvider(
        smtp_host="smtp.example.com", smtp_username="me@example.com", smtp_password="pw",
        email_to="romanvasilyev@hotmail.com",
    )
    assert provider.email_from == "me@example.com"


# ---------- MultiProvider ----------

def test_multi_provider_succeeds_if_any_channel_succeeds():
    class OkProvider:
        def send(self, notification):
            return True

    class FailProvider:
        def send(self, notification):
            return False

    provider = MultiProvider([FailProvider(), OkProvider()])
    assert provider.send(Notification(title="T", message="M")) is True


def test_multi_provider_fails_if_all_channels_fail():
    class FailProvider:
        def send(self, notification):
            return False

    provider = MultiProvider([FailProvider(), FailProvider()])
    assert provider.send(Notification(title="T", message="M")) is False


def test_multi_provider_isolates_a_raising_channel():
    class RaisingProvider:
        def send(self, notification):
            raise RuntimeError("boom")

    class OkProvider:
        def send(self, notification):
            return True

    provider = MultiProvider([RaisingProvider(), OkProvider()])
    assert provider.send(Notification(title="T", message="M")) is True


def test_multi_provider_sends_to_every_channel():
    calls = []

    class RecordingProvider:
        def __init__(self, name):
            self.name = name

        def send(self, notification):
            calls.append(self.name)
            return True

    provider = MultiProvider([RecordingProvider("a"), RecordingProvider("b")])
    provider.send(Notification(title="T", message="M"))
    assert calls == ["a", "b"]


# ---------- get_alert_provider ----------

def test_get_alert_provider_returns_console_when_disabled():
    settings = make_settings(ENABLE_NOTIFICATIONS=False)
    provider = get_alert_provider(settings)
    assert isinstance(provider, ConsoleProvider)


def test_get_alert_provider_returns_pushover_when_only_pushover_configured():
    settings = make_settings(ENABLE_NOTIFICATIONS=True, PUSHOVER_USER_KEY="u", PUSHOVER_APP_TOKEN="t")
    provider = get_alert_provider(settings)
    assert isinstance(provider, PushoverProvider)


def test_get_alert_provider_returns_email_when_only_email_configured():
    settings = make_settings(
        ENABLE_NOTIFICATIONS=True, SMTP_HOST="smtp.example.com", SMTP_USERNAME="me@example.com",
        SMTP_PASSWORD="pw", EMAIL_TO="romanvasilyev@hotmail.com",
    )
    provider = get_alert_provider(settings)
    assert isinstance(provider, EmailProvider)


def test_get_alert_provider_returns_multi_when_both_configured():
    settings = make_settings(
        ENABLE_NOTIFICATIONS=True, PUSHOVER_USER_KEY="u", PUSHOVER_APP_TOKEN="t",
        SMTP_HOST="smtp.example.com", SMTP_USERNAME="me@example.com", SMTP_PASSWORD="pw",
        EMAIL_TO="romanvasilyev@hotmail.com",
    )
    provider = get_alert_provider(settings)
    assert isinstance(provider, MultiProvider)
    assert len(provider.providers) == 2


# ---------- send_test_notification ----------

def test_send_test_notification_requires_at_least_one_channel():
    settings = make_settings()
    with pytest.raises(ValueError):
        send_test_notification(settings)


@respx.mock
def test_send_test_notification_pushover_only():
    respx.post(PUSHOVER_API_URL).mock(return_value=httpx.Response(200, json={"status": 1}))
    settings = make_settings(PUSHOVER_USER_KEY="user_key", PUSHOVER_APP_TOKEN="app_token")
    results = send_test_notification(settings)
    assert results == {"pushover": True}


@respx.mock
def test_send_test_notification_tests_both_channels(monkeypatch):
    respx.post(PUSHOVER_API_URL).mock(return_value=httpx.Response(200, json={"status": 1}))

    class FakeSMTP:
        def __init__(self, host, port, timeout=10.0):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            pass

        def login(self, username, password):
            pass

        def sendmail(self, from_addr, to_addrs, message):
            pass

    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    settings = make_settings(
        PUSHOVER_USER_KEY="user_key", PUSHOVER_APP_TOKEN="app_token",
        SMTP_HOST="smtp.example.com", SMTP_USERNAME="me@example.com", SMTP_PASSWORD="pw",
        EMAIL_TO="romanvasilyev@hotmail.com",
    )
    results = send_test_notification(settings)
    assert results == {"pushover": True, "email": True}


@respx.mock
def test_send_test_notification_ignores_enable_notifications_flag():
    """The manual test must be able to reach real Pushover even while
    ENABLE_NOTIFICATIONS is still false — that's the whole point of testing
    before flipping the flag on."""
    respx.post(PUSHOVER_API_URL).mock(return_value=httpx.Response(200, json={"status": 1}))
    settings = make_settings(ENABLE_NOTIFICATIONS=False, PUSHOVER_USER_KEY="user_key", PUSHOVER_APP_TOKEN="app_token")
    results = send_test_notification(settings)
    assert results == {"pushover": True}


# ---------- send_job_alerts ----------

def make_job(score: float, title: str = "Analyst", company: str = "Acme") -> ScoredJobForAlert:
    return ScoredJobForAlert(
        score=score, title=title, company=company, location="Sacramento, CA",
        apply_url="https://example.com/1", match_explanation="Great fit",
    )


class RecordingProvider:
    def __init__(self):
        self.sent: list[Notification] = []

    def send(self, notification: Notification) -> bool:
        self.sent.append(notification)
        return True


def test_send_job_alerts_immediate_for_high_scores():
    provider = RecordingProvider()
    jobs = [make_job(92), make_job(50)]  # 50 is below both thresholds — no alert at all
    stats = send_job_alerts(jobs, match_alert_threshold=80, immediate_alert_threshold=88, provider=provider)
    assert stats.immediate_sent == 1
    assert stats.digest_sent is False
    assert len(provider.sent) == 1
    assert "92%" in provider.sent[0].title


def test_send_job_alerts_batches_digest_range_into_one_notification():
    provider = RecordingProvider()
    jobs = [make_job(82), make_job(85), make_job(81)]  # all in [80, 88) digest range
    stats = send_job_alerts(jobs, match_alert_threshold=80, immediate_alert_threshold=88, provider=provider)
    assert stats.immediate_sent == 0
    assert stats.digest_sent is True
    assert stats.digest_count == 3
    assert len(provider.sent) == 1  # ONE combined notification, not three


def test_send_job_alerts_below_match_threshold_gets_no_alert():
    provider = RecordingProvider()
    jobs = [make_job(65)]
    stats = send_job_alerts(jobs, match_alert_threshold=80, immediate_alert_threshold=88, provider=provider)
    assert stats.immediate_sent == 0
    assert stats.digest_sent is False
    assert len(provider.sent) == 0


def test_send_job_alerts_mixes_immediate_and_digest():
    provider = RecordingProvider()
    jobs = [make_job(95), make_job(82)]
    stats = send_job_alerts(jobs, match_alert_threshold=80, immediate_alert_threshold=88, provider=provider)
    assert stats.immediate_sent == 1
    assert stats.digest_sent is True
    assert len(provider.sent) == 2


def test_send_job_alerts_tracks_failures():
    class FailingProvider:
        def send(self, notification: Notification) -> bool:
            return False

    stats = send_job_alerts([make_job(95)], match_alert_threshold=80, immediate_alert_threshold=88, provider=FailingProvider())
    assert stats.immediate_sent == 0
    assert stats.failures == 1
    assert len(stats.errors) == 1


def test_send_job_alerts_empty_list():
    provider = RecordingProvider()
    stats = send_job_alerts([], match_alert_threshold=80, immediate_alert_threshold=88, provider=provider)
    assert stats == AlertStats()
    assert provider.sent == []

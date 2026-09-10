"""Alert subscriptions, digest rendering and never-send-twice behaviour."""

from __future__ import annotations

from test_filters import seed

from acide import alerts as alerts_service
from acide import db, mailer
from acide.models import (
    AlertFilters,
    EmailConfig,
    ScoringConfig,
    SetupConfig,
)


def _config(**overrides) -> SetupConfig:
    config = SetupConfig(
        email=EmailConfig(
            enabled=True,
            smtp_server="smtp.example.com",
            sender_email="alerts@example.com",
            sender_password="secret",
        ),
        scoring=ScoringConfig(**overrides.pop("scoring", {})),
    )
    return config


class _Recorder:
    """Stand-in for `mailer.send` that captures what would go out."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    def __call__(self, config, to_address, subject, html_body, text_body):
        self.sent.append((to_address, subject, text_body))


def test_alert_round_trip():
    subscription = db.create_alert("candidate@example.com", AlertFilters(search="embedded"))
    assert subscription.id > 0
    assert db.list_alerts(email="candidate@example.com")[0].filters.search == "embedded"

    db.set_alert_active(subscription.id, False)
    assert db.list_alerts(active_only=True) == []

    assert db.delete_alert(subscription.id) is True
    assert db.list_alerts() == []


def test_digest_is_sent_once_per_job(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(mailer, "send", recorder)
    monkeypatch.setattr(alerts_service.mailer, "send", recorder)

    seed(external_id="1", title="Embedded Engineer", experience_fit_score=90)
    db.create_alert("candidate@example.com", AlertFilters(search="Embedded"))

    assert alerts_service.dispatch_pending(_config()) == 1
    assert len(recorder.sent) == 1
    assert "Embedded Engineer" in recorder.sent[0][2]

    # Second pass with nothing new must stay silent.
    assert alerts_service.dispatch_pending(_config()) == 0
    assert len(recorder.sent) == 1

    # A newly indexed match reopens the digest.
    seed(external_id="2", title="Embedded Architect", experience_fit_score=88)
    assert alerts_service.dispatch_pending(_config()) == 1
    assert len(recorder.sent) == 2
    assert "Embedded Architect" in recorder.sent[1][2]


def test_low_scoring_jobs_are_not_emailed(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(alerts_service.mailer, "send", recorder)

    seed(external_id="1", title="Weak Match", experience_fit_score=30, interest_fit_score=20)
    db.create_alert("candidate@example.com", AlertFilters())

    assert alerts_service.dispatch_pending(_config()) == 0
    assert recorder.sent == []


def test_pivot_only_match_still_alerts(monkeypatch):
    """A stretch role the candidate cannot yet do is the whole point."""
    recorder = _Recorder()
    monkeypatch.setattr(alerts_service.mailer, "send", recorder)

    seed(
        external_id="1",
        title="Cloud Security Engineer",
        experience_fit_score=35,
        interest_fit_score=96,
        category_type="Pivot / Growth Opportunity",
    )
    db.create_alert("candidate@example.com", AlertFilters())
    assert alerts_service.dispatch_pending(_config()) == 1
    assert "Cloud Security Engineer" in recorder.sent[0][2]


def test_subscription_filters_are_respected(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(alerts_service.mailer, "send", recorder)

    seed(external_id="1", title="Rome Role", location="Rome, Italy", experience_fit_score=90)
    seed(external_id="2", title="Texas Role", location="Austin, Texas", experience_fit_score=90)
    db.create_alert("candidate@example.com", AlertFilters(location="Italy"))

    alerts_service.dispatch_pending(_config())
    body = recorder.sent[0][2]
    assert "Rome Role" in body
    assert "Texas Role" not in body


def test_disabled_email_short_circuits(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(alerts_service.mailer, "send", recorder)
    seed(external_id="1", experience_fit_score=90)
    db.create_alert("candidate@example.com", AlertFilters())

    config = _config()
    config.email.enabled = False
    assert alerts_service.dispatch_pending(config) == 0
    assert recorder.sent == []


def test_smtp_failure_does_not_mark_jobs_as_sent(monkeypatch):
    def explode(*args, **kwargs):
        raise mailer.MailError("relay refused")

    monkeypatch.setattr(alerts_service.mailer, "send", explode)
    seed(external_id="1", title="Embedded Engineer", experience_fit_score=90)
    db.create_alert("candidate@example.com", AlertFilters())

    assert alerts_service.dispatch_pending(_config()) == 0

    # The ledger must stay empty so the next run retries this job.
    recorder = _Recorder()
    monkeypatch.setattr(alerts_service.mailer, "send", recorder)
    assert alerts_service.dispatch_pending(_config()) == 1


def test_erasure_removes_every_subscription_for_an_address():
    db.create_alert("candidate@example.com", AlertFilters(search="a"))
    db.create_alert("candidate@example.com", AlertFilters(search="b"))
    db.create_alert("other@example.com", AlertFilters())

    assert db.delete_alerts_for_email("candidate@example.com") == 2
    assert [item.email for item in db.list_alerts()] == ["other@example.com"]


def test_digest_html_escapes_untrusted_posting_text():
    """Job titles come from third-party feeds and are rendered into HTML."""
    seed(
        external_id="1",
        title='Engineer <script>alert("x")</script>',
        experience_fit_score=90,
    )
    job = db.list_jobs().items[0]
    _, html_body, _ = mailer.render_digest([job], "http://localhost:8000", "http://x/unsub")
    assert "<script>" not in html_body
    assert "&lt;script&gt;" in html_body


def test_digest_subject_reflects_batch_size():
    seed(external_id="1", title="Only Role", experience_fit_score=90)
    jobs = db.list_jobs().items
    subject, _, _ = mailer.render_digest(jobs, "http://x", "http://x/u")
    assert subject == "ACIDE-Watch: Only Role at Acme"

    seed(external_id="2", title="Second Role", experience_fit_score=90)
    subject, _, _ = mailer.render_digest(db.list_jobs().items, "http://x", "http://x/u")
    assert subject == "ACIDE-Watch: 2 new roles match your alert"


def test_digest_includes_unsubscribe_link():
    seed(external_id="1", experience_fit_score=90)
    jobs = db.list_jobs().items
    _, html_body, text_body = mailer.render_digest(jobs, "http://portal", "http://portal/unsub/7")
    assert "http://portal/unsub/7" in html_body
    assert "http://portal/unsub/7" in text_body

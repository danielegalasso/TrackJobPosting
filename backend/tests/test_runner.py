"""End-to-end inspector pass, with the network and the gateway stubbed."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from acide import alerts as alerts_service
from acide import db
from acide.models import (
    AlertFilters,
    EmailConfig,
    OpenRouterConfig,
    SetupConfig,
    SpiderConfig,
    TargetSource,
)
from acide.spider import runner

FIXTURES = Path(__file__).parent / "fixtures"

EVALUATION = {
    "seniority": "Senior",
    "category": "Embedded / Systems",
    "years_experience_min": 5,
    "rate": "Yearly",
    "currency": "USD",
    "amount": 0,
    "experience_fit_score": 88,
    "interest_fit_score": 40,
    "category_type": "Direct Match",
    "transferable_skills": ["Embedded C"],
    "skills_to_learn": ["RTOS internals"],
    "alert_summary": "Close to what you already ship.",
}


def _config(**overrides) -> SetupConfig:
    config = SetupConfig(
        openrouter=OpenRouterConfig(api_key="sk-test", max_concurrency=2),
        email=EmailConfig(
            enabled=True,
            smtp_server="smtp.example.com",
            sender_email="alerts@example.com",
            sender_password="secret",
        ),
        # request_delay is the politeness floor in production; the test would
        # otherwise sleep through it.
        spider=SpiderConfig(enabled=True, request_delay_seconds=0.5),
        interests=["Cloud security"],
        targets=[
            TargetSource(company="ExampleCorp", source_type="greenhouse", board_token="examplecorp")
        ],
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    config.spider.request_delay_seconds = 0.5
    return config


def _mock_board():
    respx.get("https://boards-api.greenhouse.io/v1/boards/examplecorp/jobs").mock(
        return_value=httpx.Response(
            200, json=json.loads((FIXTURES / "greenhouse_jobs.json").read_text("utf-8"))
        )
    )


def _mock_gateway():
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(EVALUATION)}}]}
        )
    )


@pytest.fixture(autouse=True)
def no_throttle(monkeypatch):
    """Skip the connector's inter-request delay so tests stay fast."""
    monkeypatch.setattr("acide.spider.base.Connector._throttle", lambda self: None)


@respx.mock
def test_full_pass_indexes_scores_and_alerts(monkeypatch):
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        alerts_service.mailer,
        "send",
        lambda config, to, subject, html, text: sent.append((to, text)),
    )
    _mock_board()
    _mock_gateway()

    db.create_alert("candidate@example.com", AlertFilters())

    summary = runner.run_once(_config())

    assert summary.sources_polled == 1
    assert summary.postings_seen == 2
    assert summary.postings_new == 2
    assert summary.postings_scored == 2
    assert summary.errors == []
    assert summary.running is False

    stored = db.list_jobs()
    assert stored.total == 2
    assert {job.company for job in stored.items} == {"ExampleCorp"}
    assert stored.items[0].experience_fit_score == 88
    # Compensation published by the ATS wins over the model's 0.
    watch_role = next(job for job in stored.items if "Watch" in job.title)
    assert watch_role.amount == pytest.approx(165_000)

    assert summary.alerts_sent == 1
    assert len(sent) == 1
    assert "Software Engineer, Watch Software" in sent[0][1]


@respx.mock
def test_second_pass_skips_already_scored_postings(monkeypatch):
    monkeypatch.setattr(alerts_service.mailer, "send", lambda *a, **k: None)
    _mock_board()
    gateway = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(EVALUATION)}}]}
        )
    )

    runner.run_once(_config())
    assert gateway.call_count == 2

    second = runner.run_once(_config())
    # Inference is the expensive step: seen again, but not re-scored.
    assert second.postings_seen == 2
    assert second.postings_new == 0
    assert gateway.call_count == 2


@respx.mock
def test_an_unreachable_source_is_reported_not_fatal(monkeypatch):
    monkeypatch.setattr(alerts_service.mailer, "send", lambda *a, **k: None)
    respx.get("https://boards-api.greenhouse.io/v1/boards/examplecorp/jobs").mock(
        return_value=httpx.Response(200, json=json.loads(
            (FIXTURES / "greenhouse_jobs.json").read_text("utf-8")
        ))
    )
    respx.get("https://api.lever.co/v0/postings/broken").mock(
        return_value=httpx.Response(503)
    )
    _mock_gateway()

    config = _config()
    config.targets.append(
        TargetSource(company="Broken", source_type="lever", board_token="broken")
    )

    summary = runner.run_once(config)

    assert summary.sources_polled == 1  # the healthy one still ran
    assert summary.postings_scored == 2
    assert any("Broken" in error for error in summary.errors)


@respx.mock
def test_a_failed_evaluation_does_not_lose_the_other_postings(monkeypatch):
    monkeypatch.setattr(alerts_service.mailer, "send", lambda *a, **k: None)
    _mock_board()

    responses = [
        httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(EVALUATION)}}]}),
        httpx.Response(500, json={"error": "model unavailable"}),
    ]
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        side_effect=lambda request: responses.pop(0) if responses else responses
    )

    summary = runner.run_once(_config())
    assert summary.postings_scored == 1
    assert len(summary.errors) == 1
    assert db.list_jobs().total == 1


@respx.mock
def test_missing_api_key_stops_before_scoring(monkeypatch):
    monkeypatch.setattr(alerts_service.mailer, "send", lambda *a, **k: None)
    _mock_board()

    config = _config()
    config.openrouter.api_key = ""

    summary = runner.run_once(config)
    assert summary.postings_new == 2
    assert summary.postings_scored == 0
    assert any("OpenRouter API key" in error for error in summary.errors)
    assert db.list_jobs().total == 0


def test_no_enabled_targets_is_reported_clearly():
    config = _config()
    config.targets = []
    summary = runner.run_once(config, send_alerts=False)
    assert summary.errors == ["no enabled targets configured"]


@respx.mock
def test_disabled_targets_are_skipped(monkeypatch):
    monkeypatch.setattr(alerts_service.mailer, "send", lambda *a, **k: None)
    config = _config()
    config.targets[0].enabled = False
    summary = runner.run_once(config, send_alerts=False)
    assert summary.sources_polled == 0
    assert summary.errors == ["no enabled targets configured"]

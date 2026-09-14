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
    # Both were stored when they were found; only one carries a verdict. The
    # unscored one stays in the queue instead of being lost with the failure.
    assert db.list_jobs().total == 2
    assert db.list_jobs(scored="scored").total == 1
    assert db.count_unscored() == 1


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
    # Crawling and judging are separate concerns now: without a key the
    # postings are still found and kept, waiting to be scored later.
    assert db.count_unscored() == 2
    assert db.list_jobs(scored="scored").total == 0


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


@respx.mock
def test_a_source_that_fails_does_not_cost_the_ones_already_scored():
    """A run over several hundred sources takes hours, rendered careers pages
    most of all. Collecting everything before scoring meant an interruption at
    hour four lost hour one; each source is now persisted as it finishes, so a
    failure costs only the source in flight."""
    _mock_board()
    respx.get("https://api.lever.co/v0/postings/broken").mock(return_value=httpx.Response(500))
    _mock_gateway()

    config = _config()
    config.targets = [
        TargetSource(company="ExampleCorp", source_type="greenhouse", board_token="examplecorp"),
        TargetSource(company="Broken", source_type="lever", board_token="broken"),
    ]
    summary = runner.run_once(config, send_alerts=False)

    # The healthy source reached the database despite the later failure.
    stored = db.list_jobs(limit=50)
    companies = {job.company for job in stored.items}
    assert "ExampleCorp" in companies
    assert summary.postings_scored >= 1
    assert any("Broken" in error for error in summary.errors)


@respx.mock
def test_every_source_is_scored_not_only_the_first():
    """Streaming per source must not drop the later ones."""
    _mock_board()
    respx.get("https://boards-api.greenhouse.io/v1/boards/second/jobs").mock(
        return_value=httpx.Response(200, json={"jobs": [{
            "id": 99,
            "title": "Detection Engineer",
            "content": "<p>Tune rules.</p>",
            "absolute_url": "https://example.com/99",
            "location": {"name": "Rome"},
        }]})
    )
    _mock_gateway()

    config = _config()
    config.targets = [
        TargetSource(company="ExampleCorp", source_type="greenhouse", board_token="examplecorp"),
        TargetSource(company="Second", source_type="greenhouse", board_token="second"),
    ]
    summary = runner.run_once(config, send_alerts=False)

    assert summary.sources_polled == 2
    companies = {job.company for job in db.list_jobs(limit=50).items}
    assert {"ExampleCorp", "Second"} <= companies


@respx.mock
def test_the_progress_log_numbers_each_source():
    """A run of several hundred is unreadable without it."""
    from acide.logbus import bus

    _mock_board()
    _mock_gateway()
    runner.run_once(_config(), send_alerts=False)
    lines = [event["message"] for event in bus.history()]
    assert any("[1/1] ExampleCorp" in line for line in lines), lines


# ---------------------------------------------------------------------------
# `acide inspect` — one pass, no server
# ---------------------------------------------------------------------------
@respx.mock
def test_inspect_runs_a_pass_and_reports_it(monkeypatch, capsys):
    """An overnight batch cannot depend on the server and a browser tab."""
    from acide import config as config_module
    from acide.__main__ import main

    _mock_board()
    _mock_gateway()
    config_module.save(_config())

    monkeypatch.setattr("sys.argv", ["acide", "inspect", "--no-alerts"])
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 0

    printed = capsys.readouterr().out
    assert "Inspecting 1 source(s): 1 greenhouse" in printed
    assert "postings scored" in printed


@respx.mock
def test_inspect_can_be_limited_to_one_source_type(monkeypatch, capsys):
    """So the slow rendered pages can be run apart from the fast APIs."""
    from acide import config as config_module
    from acide.__main__ import main

    _mock_board()
    _mock_gateway()
    config = _config()
    config.targets = [
        TargetSource(company="ExampleCorp", source_type="greenhouse", board_token="examplecorp"),
        TargetSource(company="Rendered", source_type="browser",
                     board_token="https://acme.example/careers"),
    ]
    config_module.save(config)

    monkeypatch.setattr(
        "sys.argv", ["acide", "inspect", "--no-alerts", "--source-type", "greenhouse"]
    )
    with pytest.raises(SystemExit):
        main()
    printed = capsys.readouterr().out
    assert "Inspecting 1 source(s): 1 greenhouse" in printed
    assert "Rendered" not in printed


def test_inspect_warns_when_a_corporate_board_has_no_search_terms(monkeypatch, capsys):
    """Without terms the cap is spent on an arbitrary slice and billed for."""
    from acide import config as config_module
    from acide.__main__ import main

    config = _config()
    config.spider.search_terms = []
    config.targets = [
        TargetSource(company="Thales", source_type="workday",
                     board_token="thales.wd3.myworkdayjobs.com/Careers")
    ]
    config_module.save(config)

    # No board is mocked, so the run fails — the warning must precede it.
    monkeypatch.setattr("sys.argv", ["acide", "inspect", "--no-alerts"])
    with pytest.raises(SystemExit):
        main()
    assert "search_terms is empty" in capsys.readouterr().out


def test_inspect_says_so_when_nothing_matches(monkeypatch, capsys):
    from acide import config as config_module
    from acide.__main__ import main

    config_module.save(_config())
    monkeypatch.setattr(
        "sys.argv", ["acide", "inspect", "--source-type", "breezy"]
    )
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 1
    assert "nothing to inspect" in capsys.readouterr().err


@respx.mock
def test_inspect_stops_before_crawling_when_the_evaluator_is_broken(monkeypatch, capsys):
    """A 469-source run crawled for four hours and scored nothing, because the
    request every posting makes was rejected before a token was generated. One
    cheap call up front finds that in seconds."""
    from acide import config as config_module
    from acide.__main__ import main

    _mock_board()
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(400, json={"error": {
            "message": "Invalid schema for response_format 'job_evaluation'"
        }})
    )
    config_module.save(_config())

    monkeypatch.setattr("sys.argv", ["acide", "inspect", "--no-alerts"])
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 1

    captured = capsys.readouterr()
    assert "Invalid schema" in captured.err
    assert "Nothing was crawled" in captured.err
    # The board must not have been touched at all.
    assert not respx.routes[0].called


@respx.mock
def test_inspect_crawls_when_the_evaluator_answers(monkeypatch, capsys):
    from acide import config as config_module
    from acide.__main__ import main

    _mock_board()
    _mock_gateway()
    config_module.save(_config())

    monkeypatch.setattr("sys.argv", ["acide", "inspect", "--no-alerts"])
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 0
    assert "evaluator ok." in capsys.readouterr().out


@respx.mock
def test_skip_preflight_crawls_without_testing_first(monkeypatch, capsys):
    from acide import config as config_module
    from acide.__main__ import main

    _mock_board()
    _mock_gateway()
    config_module.save(_config())

    monkeypatch.setattr(
        "sys.argv", ["acide", "inspect", "--no-alerts", "--skip-preflight"]
    )
    with pytest.raises(SystemExit):
        main()
    assert "Checking the evaluator" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# `acide score` — judging what was already crawled
# ---------------------------------------------------------------------------
@respx.mock
def test_score_is_a_dry_run_until_yes(monkeypatch, capsys):
    """One model call per posting is a bill; it should not start by accident."""
    from acide import config as config_module
    from acide.__main__ import main

    _mock_board()
    config = _config()
    config.openrouter.api_key = ""
    config_module.save(config)
    runner.run_once(config, send_alerts=False)   # crawls, cannot score
    assert db.count_unscored() == 2

    config.openrouter.api_key = "sk-test"
    config_module.save(config)
    gateway = respx.post("https://openrouter.ai/api/v1/chat/completions")

    monkeypatch.setattr("sys.argv", ["acide", "score"])
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 0
    assert "2 posting(s) waiting" in capsys.readouterr().out
    assert not gateway.called, "a dry run must not spend anything"
    assert db.count_unscored() == 2


@respx.mock
def test_score_judges_the_backlog_without_crawling_again(monkeypatch, capsys):
    """The point: 2,649 postings already fetched need judging, not re-fetching."""
    from acide import config as config_module
    from acide.__main__ import main

    _mock_board()
    config = _config()
    config.openrouter.api_key = ""
    config_module.save(config)
    runner.run_once(config, send_alerts=False)

    config.openrouter.api_key = "sk-test"
    config_module.save(config)
    board = respx.get("https://boards-api.greenhouse.io/v1/boards/examplecorp/jobs")
    board.reset()
    _mock_gateway()

    monkeypatch.setattr("sys.argv", ["acide", "score", "--yes"])
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 0

    printed = capsys.readouterr().out
    assert "scored     2" in printed
    assert db.count_unscored() == 0
    assert db.list_jobs(scored="scored").total == 2


@respx.mock
def test_score_stops_when_the_evaluator_is_broken(monkeypatch, capsys):
    from acide import config as config_module
    from acide.__main__ import main

    _mock_board()
    config = _config()
    config.openrouter.api_key = ""
    config_module.save(config)
    runner.run_once(config, send_alerts=False)

    config.openrouter.api_key = "sk-test"
    config_module.save(config)
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(400, json={"error": {"message": "Invalid schema"}})
    )

    monkeypatch.setattr("sys.argv", ["acide", "score", "--yes"])
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 1
    assert "Nothing was scored" in capsys.readouterr().err
    assert db.count_unscored() == 2, "the backlog is untouched"


def test_score_says_so_when_there_is_no_backlog(monkeypatch, capsys):
    from acide import config as config_module
    from acide.__main__ import main

    config_module.save(_config())
    monkeypatch.setattr("sys.argv", ["acide", "score"])
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 0
    assert "nothing is waiting" in capsys.readouterr().out

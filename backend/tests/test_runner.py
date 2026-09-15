"""End-to-end inspector pass, with the network and the gateway stubbed."""

from __future__ import annotations

import json
from datetime import UTC, datetime
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
    RawPosting,
    SetupConfig,
    SpiderConfig,
    SpiderRunSummary,
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


@respx.mock
def test_no_score_stores_everything_and_calls_nobody():
    """The hours and the bill are separate: crawl once, judge afterwards."""
    _mock_board()
    gateway = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(500)
    )

    summary = runner.run_once(_config(), send_alerts=False, score=False)

    assert not gateway.called, "a crawl-only pass must not reach the evaluator"
    assert summary.postings_new == db.count_unscored() > 0
    assert summary.postings_scored == 0
    assert not summary.errors, summary.errors


@respx.mock
def test_no_score_leaves_a_backlog_score_can_finish():
    """What --no-score stores is exactly what `acide score` later judges."""
    _mock_board()
    runner.run_once(_config(), send_alerts=False, score=False)
    waiting = db.count_unscored()

    _mock_gateway()
    summary = SpiderRunSummary(started_at=datetime.now(UTC))
    runner._score(db.unscored_postings(limit=waiting), _config(), "", summary, None)

    assert summary.postings_scored == waiting
    assert db.count_unscored() == 0


@respx.mock
def test_no_score_needs_no_evaluator_preflight(monkeypatch, capsys):
    """Testing a model this run will never call would only be a way to fail."""
    from acide import config as config_module
    from acide.__main__ import main

    _mock_board()
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(400, json={"error": {"message": "Invalid schema"}})
    )
    config_module.save(_config())

    monkeypatch.setattr("sys.argv", ["acide", "inspect", "--no-alerts", "--no-score"])
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 0

    printed = capsys.readouterr().out
    assert "Checking the evaluator" not in printed
    assert "waiting to be scored" in printed
    assert "acide score" in printed


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


# ---------------------------------------------------------------------------
# Remembering how each source went, so only the failures need repeating
# ---------------------------------------------------------------------------
@respx.mock
def test_each_source_records_how_it_went():
    _mock_board()
    respx.get("https://api.lever.co/v0/postings/broken").mock(return_value=httpx.Response(500))
    _mock_gateway()

    config = _config()
    config.targets = [
        TargetSource(company="ExampleCorp", source_type="greenhouse", board_token="examplecorp"),
        TargetSource(company="Broken", source_type="lever", board_token="broken"),
    ]
    runner.run_once(config, send_alerts=False)

    states = {row["company"]: row for row in db.source_states()}
    assert states["ExampleCorp"]["status"] == "ok"
    assert states["ExampleCorp"]["postings"] == 2
    assert states["Broken"]["status"] == "error"
    assert "500" in states["Broken"]["detail"]

    assert db.succeeded_source_keys() == {("greenhouse", "examplecorp")}
    assert db.failed_source_keys() == {("lever", "broken")}


@respx.mock
def test_retry_failed_skips_what_already_worked(monkeypatch, capsys):
    """A pass over several hundred rendered pages costs hours; fixing the few
    that broke must not mean repeating the ones that did not."""
    from acide import config as config_module
    from acide.__main__ import main

    _mock_board()
    respx.get("https://api.lever.co/v0/postings/broken").mock(return_value=httpx.Response(500))
    _mock_gateway()

    config = _config()
    config.targets = [
        TargetSource(company="ExampleCorp", source_type="greenhouse", board_token="examplecorp"),
        TargetSource(company="Broken", source_type="lever", board_token="broken"),
    ]
    config_module.save(config)
    runner.run_once(config, send_alerts=False)

    board = respx.get("https://boards-api.greenhouse.io/v1/boards/examplecorp/jobs")
    board.reset()

    monkeypatch.setattr(
        "sys.argv", ["acide", "inspect", "--no-alerts", "--retry-failed", "--skip-preflight"]
    )
    with pytest.raises(SystemExit):
        main()

    printed = capsys.readouterr().out
    assert "Skipping 1 source(s) that already succeeded" in printed
    assert not board.called, "the healthy source must not be crawled again"


@respx.mock
def test_a_source_never_attempted_counts_as_unfinished(monkeypatch, capsys):
    """So an interrupted pass resumes rather than restarts."""
    from acide import config as config_module
    from acide.__main__ import main

    _mock_board()
    _mock_gateway()
    config = _config()
    config_module.save(config)
    runner.run_once(config, send_alerts=False)  # only ExampleCorp exists so far

    config.targets = [
        TargetSource(company="ExampleCorp", source_type="greenhouse", board_token="examplecorp"),
        TargetSource(company="Fresh", source_type="greenhouse", board_token="fresh"),
    ]
    config_module.save(config)
    respx.get("https://boards-api.greenhouse.io/v1/boards/fresh/jobs").mock(
        return_value=httpx.Response(200, json={"jobs": []})
    )

    monkeypatch.setattr(
        "sys.argv", ["acide", "inspect", "--no-alerts", "--retry-failed", "--skip-preflight"]
    )
    with pytest.raises(SystemExit):
        main()
    assert "Skipping 1 source(s)" in capsys.readouterr().out
    assert ("greenhouse", "fresh") in db.succeeded_source_keys()


@respx.mock
def test_sources_reports_the_state_and_what_to_run(monkeypatch, capsys):
    from acide import config as config_module
    from acide.__main__ import main

    _mock_board()
    respx.get("https://api.lever.co/v0/postings/broken").mock(return_value=httpx.Response(500))
    _mock_gateway()

    config = _config()
    config.targets = [
        TargetSource(company="ExampleCorp", source_type="greenhouse", board_token="examplecorp"),
        TargetSource(company="Broken", source_type="lever", board_token="broken"),
        TargetSource(company="Untouched", source_type="ashby", board_token="untouched"),
    ]
    config_module.save(config)
    config.targets = config.targets[:2]
    runner.run_once(config, send_alerts=False)

    monkeypatch.setattr("sys.argv", ["acide", "sources"])
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 0

    printed = capsys.readouterr().out
    assert "1  succeeded" in printed
    assert "1  failed" in printed
    assert "1  never attempted" in printed
    assert "Broken" in printed
    assert "Untouched" in printed
    assert "acide inspect --retry-failed" in printed


def test_sources_says_so_before_anything_has_run(monkeypatch, capsys):
    from acide import config as config_module
    from acide.__main__ import main

    config_module.save(_config())
    monkeypatch.setattr("sys.argv", ["acide", "sources"])
    with pytest.raises(SystemExit):
        main()
    assert "no source has been crawled yet" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Schema currency — a database written before a column existed
# ---------------------------------------------------------------------------
#: The `jobs` table exactly as it was before the `scored` column, which is what
#: a database built by an earlier release actually contains.
_OLD_JOBS_TABLE = """
CREATE TABLE jobs (
    id                    TEXT PRIMARY KEY,
    external_id           TEXT NOT NULL,
    source_type           TEXT NOT NULL DEFAULT '',
    title                 TEXT NOT NULL,
    company               TEXT NOT NULL,
    location              TEXT NOT NULL DEFAULT '',
    seniority             TEXT NOT NULL DEFAULT 'Mid-Level',
    category              TEXT NOT NULL DEFAULT 'General',
    years_experience_min  INTEGER NOT NULL DEFAULT 0,
    date_posted           TEXT,
    rate                  TEXT NOT NULL DEFAULT 'Yearly',
    currency              TEXT NOT NULL DEFAULT 'USD',
    amount                REAL NOT NULL DEFAULT 0.0,
    experience_fit_score  INTEGER NOT NULL DEFAULT 0,
    interest_fit_score    INTEGER NOT NULL DEFAULT 0,
    category_type         TEXT NOT NULL DEFAULT 'Unrelated',
    transferable_skills   TEXT NOT NULL DEFAULT '[]',
    skills_to_learn       TEXT NOT NULL DEFAULT '[]',
    alert_summary         TEXT NOT NULL DEFAULT '',
    apply_url             TEXT NOT NULL,
    saved                 INTEGER NOT NULL DEFAULT 0,
    dismissed             INTEGER NOT NULL DEFAULT 0,
    created_at            TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at            TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _database_from_before_the_scored_column() -> None:
    """Replace the test database with one an earlier release would have left."""
    import sqlite3

    from acide import paths

    db.reset_connection()
    paths.DB_PATH.unlink(missing_ok=True)
    conn = sqlite3.connect(str(paths.DB_PATH))
    conn.executescript(_OLD_JOBS_TABLE)
    conn.execute(
        "INSERT INTO jobs (id, external_id, title, company, apply_url, "
        "experience_fit_score, saved) VALUES ('old:1','1','Analyst','Acme',"
        "'https://acme.test/1', 88, 1)"
    )
    conn.commit()
    conn.close()
    db.reset_connection()


def test_a_crawl_migrates_a_database_written_before_the_scored_column():
    """An overnight pass died nine seconds in on `no column named scored`.

    The migration was right; nothing in the crawl path ever ran it, because
    only `acide serve`, `--retry-failed` and `acide sources` called init_db.
    Connecting has to be enough — no entry point should have to remember.
    """
    _database_from_before_the_scored_column()

    posting = RawPosting(
        external_id="42", company="ExampleCorp", title="SOC Analyst",
        location="Milan", apply_url="https://example.test/42", source_type="greenhouse",
    )
    _, is_new = db.store_posting(posting)          # this is what raised
    assert is_new
    assert db.count_unscored() == 1

    # The row that was already there keeps its verdict and the operator's flag,
    # and counts as scored — which is what the old code guaranteed.
    existing = db.get_job("old:1")
    assert existing is not None
    assert existing.experience_fit_score == 88
    assert existing.saved is True


def test_a_crawl_creates_tables_a_database_from_before_them_lacks():
    """source_runs arrived later still; recording an outcome must not need a
    separate init either."""
    _database_from_before_the_scored_column()

    db.record_source_run("greenhouse", "examplecorp", "ExampleCorp", status="ok", postings=3)
    assert db.succeeded_source_keys() == {("greenhouse", "examplecorp")}


# ---------------------------------------------------------------------------
# A summary that says what went wrong, not just how often
# ---------------------------------------------------------------------------


@respx.mock
def test_the_summary_groups_a_repeated_fault_and_still_shows_the_rare_one(monkeypatch, capsys):
    """One bad schema must not bury the one source whose URL had rotted.

    The overnight run this reproduces reported 2,798 errors and printed
    fifteen copies of the same one; the single dead careers page was in the
    2,783 it did not print.
    """
    monkeypatch.setattr(alerts_service.mailer, "send", lambda *a, **k: None)
    from acide import config as config_module
    from acide.__main__ import main

    _mock_board()
    respx.get("https://boards-api.greenhouse.io/v1/boards/tum/jobs").mock(
        return_value=httpx.Response(404)
    )
    # Every scoring call is rejected before a token is generated, which is
    # exactly what a `required` list missing a key does.
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(400, json={"error": {"message": "Provider returned error"}})
    )

    config = _config()
    config.targets = [
        TargetSource(company="ExampleCorp", source_type="greenhouse", board_token="examplecorp"),
        TargetSource(company="TU Munchen", source_type="greenhouse", board_token="tum"),
    ]
    config_module.save(config)

    monkeypatch.setattr(
        "sys.argv", ["acide", "inspect", "--no-alerts", "--skip-preflight"]
    )
    with pytest.raises(SystemExit):
        main()

    printed = capsys.readouterr().out
    assert "3 error(s), 2 distinct cause(s)" in printed
    # The repeated fault is counted once …
    assert "2 ×" in printed
    # … and the single rotted source is named rather than summarised away.
    assert "TU Munchen" in printed

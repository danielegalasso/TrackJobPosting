"""The job inspector: fetch → score → store → alert.

One run walks every enabled target, asks each connector for its current
postings, sends only the *unseen* ones to the evaluator (inference is the
expensive step), stores the verdicts, then hands matching jobs to the alert
daemon.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

import httpx

from .. import alerts as alerts_module
from .. import config as config_module
from .. import db, resume
from ..llm import InferenceError, OpenRouterClient
from ..logbus import bus
from ..models import RawPosting, SetupConfig, SpiderRunSummary
from .base import DEFAULT_TIMEOUT, ConnectorError, get_connector

_run_lock = threading.Lock()
_state: SpiderRunSummary = SpiderRunSummary(started_at=datetime.now(UTC))


def current_state() -> SpiderRunSummary:
    return _state


def is_running() -> bool:
    return _state.running


def run_once(config: SetupConfig | None = None, *, send_alerts: bool = True) -> SpiderRunSummary:
    """Execute one full inspection pass. Only one may run at a time."""
    global _state

    if not _run_lock.acquire(blocking=False):
        bus.publish("A spider run is already in progress; skipping this trigger.", "warn")
        return _state

    config = config or config_module.load(refresh=True)
    summary = SpiderRunSummary(started_at=datetime.now(UTC), running=True)
    _state = summary

    try:
        targets = [target for target in config.targets if target.enabled]
        if not targets:
            summary.errors.append("no enabled targets configured")
            bus.publish("No enabled targets in setup.json — nothing to inspect.", "warn")
            return summary

        cv_text = resume.active_text()
        if not cv_text:
            bus.publish(
                "No CV uploaded — experience scores will be weak. Upload one in Settings.",
                "warn",
            )

        bus.publish(f"Inspection started across {len(targets)} source(s).", "info")
        postings = _collect(targets, config, summary)
        if postings:
            _score(postings, config, cv_text, summary)

        if send_alerts:
            summary.alerts_sent = alerts_module.dispatch_pending(config)

        bus.publish(
            f"Inspection finished: {summary.postings_seen} seen, "
            f"{summary.postings_new} new, {summary.postings_scored} scored, "
            f"{summary.alerts_sent} alert email(s) sent.",
            "success",
        )
        return summary
    finally:
        summary.running = False
        summary.finished_at = datetime.now(UTC)
        _run_lock.release()


def _collect(targets, config: SetupConfig, summary: SpiderRunSummary) -> list[RawPosting]:
    """Fetch every enabled board and keep only postings we have not scored."""
    unseen: list[RawPosting] = []
    headers = {"User-Agent": config.spider.user_agent, "Accept": "application/json"}

    with httpx.Client(timeout=DEFAULT_TIMEOUT, headers=headers, follow_redirects=True) as client:
        for target in targets:
            try:
                connector_cls = get_connector(target.source_type)
            except ConnectorError as exc:
                summary.errors.append(str(exc))
                bus.publish(str(exc), "error")
                continue

            connector = connector_cls(
                client,
                request_delay=config.spider.request_delay_seconds,
                max_jobs=config.spider.max_jobs_per_source,
                on_log=bus.publish,
            )
            try:
                fetched = list(connector.fetch(target))
            except ConnectorError as exc:
                summary.errors.append(f"{target.company}: {exc}")
                bus.publish(f"{target.company}: {exc}", "error")
                continue
            except Exception as exc:  # pragma: no cover - connector bug guard
                summary.errors.append(f"{target.company}: unexpected error: {exc}")
                bus.publish(f"{target.company}: unexpected error: {exc}", "error")
                continue

            summary.sources_polled += 1
            summary.postings_seen += len(fetched)

            known = db.known_external_ids(target.source_type, target.company)
            fresh = [posting for posting in fetched if posting.external_id not in known]
            summary.postings_new += len(fresh)
            unseen.extend(fresh)
            bus.publish(
                f"{target.company}: {len(fetched)} listed, {len(fresh)} new since last run.",
                "info",
            )
    return unseen


def _score(
    postings: list[RawPosting],
    config: SetupConfig,
    cv_text: str,
    summary: SpiderRunSummary,
) -> None:
    """Evaluate new postings in parallel and persist the verdicts."""
    if not config.openrouter.api_key:
        summary.errors.append("no OpenRouter API key configured — postings were not scored")
        bus.publish("No OpenRouter API key configured; skipping scoring.", "error")
        return

    bus.publish(f"Scoring {len(postings)} new posting(s) via {config.openrouter.model}.", "info")
    workers = max(1, min(config.openrouter.max_concurrency, len(postings)))

    with OpenRouterClient(config) as client, ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(client.evaluate, posting, cv_text): posting for posting in postings
        }
        for future in as_completed(futures):
            posting = futures[future]
            try:
                evaluation = future.result()
            except InferenceError as exc:
                summary.errors.append(f"{posting.title}: {exc}")
                bus.publish(f"Scoring failed for '{posting.title}': {exc}", "error")
                continue
            except Exception as exc:  # pragma: no cover - defensive
                summary.errors.append(f"{posting.title}: {exc}")
                bus.publish(f"Scoring failed for '{posting.title}': {exc}", "error")
                continue

            db.upsert_job(posting, evaluation)
            summary.postings_scored += 1
            if evaluation.is_interesting(config.scoring):
                bus.publish(
                    f"MATCH {posting.company} — {posting.title} "
                    f"(exp {evaluation.experience_fit_score}%, "
                    f"pivot {evaluation.interest_fit_score}%)",
                    "success",
                )

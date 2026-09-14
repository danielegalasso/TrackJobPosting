"""The job inspector: fetch → score → store → alert.

One run walks every enabled target, asks each connector for its current
postings, sends only the *unseen* ones to the evaluator (inference is the
expensive step), stores the verdicts, then hands matching jobs to the alert
daemon.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable
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

        # Score and persist each source as it finishes, rather than collecting
        # everything and scoring at the end. A list of several hundred sources
        # — rendered careers pages especially — takes hours, and holding all of
        # it in memory means an interruption at hour four loses hour one. This
        # way a stop costs at most the source in flight, and matches appear in
        # the portal while the run is still going.
        with contextlib.ExitStack() as stack:
            scorer = None
            if config.openrouter.api_key:
                scorer = stack.enter_context(OpenRouterClient(config))
            else:
                summary.errors.append(
                    "no OpenRouter API key configured — postings were not scored"
                )
                bus.publish("No OpenRouter API key configured; skipping scoring.", "error")

            def score_batch(batch: list[RawPosting]) -> None:
                if scorer is not None and batch:
                    _score(batch, config, cv_text, summary, scorer)

            _collect(targets, config, summary, on_batch=score_batch)

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


class _RobotsAwareSession:
    """Wraps a browser session so indexing honours robots.txt as well.

    Discovery already checks it; rendering a page to read its postings is no
    more permitted than rendering it to find its board, and the check would
    otherwise be skipped on exactly the path that visits the most pages.
    """

    def __init__(self, session: object) -> None:
        from ..browser_discovery import RobotsCache

        self._session = session
        self._robots = RobotsCache()

    def render(self, url: str, *, expand: bool = False) -> str:
        return self._session.render(url, expand=expand, robots=self._robots)  # type: ignore[attr-defined]


@contextlib.contextmanager
def _rendering_session(targets, config: SetupConfig):
    """A browser, but only when a target actually asks to be rendered.

    Every other source reads a published endpoint, and the scheduled run must
    not start a browser for them. So one is started only if a `browser` target
    is configured, and it is shared by all of them.
    """
    if not any(target.source_type == "browser" for target in targets):
        yield None
        return
    try:
        from ..browser_discovery import BrowserSession, BrowserUnavailable
    except ImportError:  # pragma: no cover - defensive
        yield None
        return
    try:
        with BrowserSession(headless=True) as session:
            bus.publish("browser started for rendered careers pages", "info")
            yield session
    except BrowserUnavailable as exc:
        bus.publish(f"browser indexing unavailable: {exc}", "error")
        yield None


def _collect(
    targets,
    config: SetupConfig,
    summary: SpiderRunSummary,
    on_batch: Callable[[list[RawPosting]], None] | None = None,
) -> list[RawPosting]:
    """Fetch every enabled board and keep only postings we have not scored.

    `on_batch` is called with each source's new postings as that source
    finishes, so a long run persists progressively. Without it the postings
    are accumulated and returned, which is only useful for a short run.
    """
    unseen: list[RawPosting] = []
    total = len(targets)
    headers = {"User-Agent": config.spider.user_agent, "Accept": "application/json"}

    with (
        httpx.Client(timeout=DEFAULT_TIMEOUT, headers=headers, follow_redirects=True) as client,
        _rendering_session(targets, config) as rendering,
    ):
        for index, target in enumerate(targets, start=1):
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
                # A per-target list wins; otherwise the global one applies.
                search_terms=target.search_terms or config.spider.search_terms,
                on_log=bus.publish,
            )
            # Only the rendered source has this attribute to set.
            if getattr(connector, "session", "absent") is None:
                connector.session = _RobotsAwareSession(rendering) if rendering else None
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

            # Store every posting the moment it is found. Scoring can fail —
            # a bad schema, an exhausted balance, a rate limit — and when it
            # does, hours of crawling must not go with it. It also means the
            # portal holds everything that was found, so filtering can happen
            # there rather than having to be guessed at collection time.
            fresh: list[RawPosting] = []
            for posting in fetched:
                _, is_new = db.store_posting(posting)
                if is_new:
                    fresh.append(posting)
            summary.postings_new += len(fresh)
            bus.publish(
                f"[{index}/{total}] {target.company}: {len(fetched)} listed, "
                f"{len(fresh)} new since last run.",
                "info",
            )
            if on_batch is None:
                unseen.extend(fresh)
            else:
                on_batch(fresh)
    return unseen


def _score(
    postings: list[RawPosting],
    config: SetupConfig,
    cv_text: str,
    summary: SpiderRunSummary,
    client: OpenRouterClient | None = None,
) -> None:
    """Evaluate new postings in parallel and persist the verdicts.

    `client` is shared across a whole run: several hundred sources would
    otherwise open and close one connection pool each.
    """
    if not config.openrouter.api_key:
        summary.errors.append("no OpenRouter API key configured — postings were not scored")
        bus.publish("No OpenRouter API key configured; skipping scoring.", "error")
        return

    bus.publish(f"Scoring {len(postings)} new posting(s) via {config.openrouter.model}.", "info")
    workers = max(1, min(config.openrouter.max_concurrency, len(postings)))

    with contextlib.ExitStack() as stack:
        if client is None:
            client = stack.enter_context(OpenRouterClient(config))
        pool = stack.enter_context(ThreadPoolExecutor(max_workers=workers))
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

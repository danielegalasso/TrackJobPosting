"""Run the Search: every extractor, then import, rank and review what is new.

One run, started from the portal:

1. **Extract** — `ExtractFromTheCarrerPageTheJobs/run_all.py`, several sites
   at a time, its per-site lines streamed to the console as they finish.
2. **Import** — each changed `latest.json` is mirrored into the jobs table
   (extracts.py): new postings appear, and those a site no longer lists are
   closed, which takes them out of the search.
3. **Rank** — the rules rank what changed, and decide which postings are
   not worth a review (a visa, a senior role, ...).
4. **Review** — every new posting worth it is scored by the model against
   the brief. Rescoring after the brief changed is a separate, explicit step
   (it can cost dollars rather than cents), started from the same panel.

What is new and what is gone is read from the table afterwards, by time,
rather than from the import's own report: a page load during the run
imports too, and would otherwise take those counts with it.
"""

from __future__ import annotations

import contextlib
import os
import re
import signal
import subprocess
import sys
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from . import db, extracts, paths, ranking
from . import config as config_module
from .logbus import bus

#: Sites extracted at the same time. Each is a process of its own, mostly
#: waiting on the network; a few start a headless browser.
DEFAULT_JOBS = 6

#: "Airbus         OK              812/812  +3 -1        95s", as run_all prints it.
_SITE_LINE = re.compile(
    r"^(?P<name>\S+)\s+(?P<status>OK|CHECK|INCOMPLETE|EMPTY|FAILED)\s+(?P<counts>\S+)"
    r"\s+(?:\+(?P<new>\d+) -(?P<gone>\d+))?\s*(?P<seconds>\d+)s$"
)


@dataclass
class SiteResult:
    name: str
    status: str
    counts: str
    new: int | None
    gone: int | None
    seconds: int


@dataclass
class CompanyChange:
    company: str
    new: int
    gone: int
    #: Of the new ones, how many the rules keep from review.
    new_excluded: int = 0


@dataclass
class SearchRun:
    running: bool = False
    #: idle, extracting, importing, ranking, reviewing, done, failed, stopped
    phase: str = "idle"
    started_at: str = ""
    finished_at: str = ""
    sites_total: int = 0
    sites: list[SiteResult] = field(default_factory=list)
    changes: list[CompanyChange] = field(default_factory=list)
    new_total: int = 0
    gone_total: int = 0
    reviewed: int = 0
    review_cost: float = 0.0
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


state = SearchRun()
_lock = threading.Lock()
_stop = threading.Event()
_process: subprocess.Popen[str] | None = None


def is_running() -> bool:
    return state.running


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _run_all() -> list[str]:
    return [sys.executable, str(paths.EXTRACTS_DIR / "run_all.py")]


def start(jobs: int = DEFAULT_JOBS, review: bool = True) -> bool:
    """Start a run in the background; False when one is already going."""
    global state
    if not _lock.acquire(blocking=False):
        return False
    if ranking.review_running():
        _lock.release()
        return False
    _stop.clear()
    state = SearchRun(running=True, phase="extracting", started_at=_now())
    threading.Thread(target=_guarded, args=(jobs, review), name="search", daemon=True).start()
    return True


def stop() -> None:
    """Stop the run: the extractors still running are killed, the review ends
    after the calls already sent. What was saved so far stays."""
    _stop.set()
    ranking.stop_review()
    process = _process
    if process and process.poll() is None:
        # run_all starts one process per site: stop the whole group.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGTERM)


def _guarded(jobs: int, review: bool) -> None:
    try:
        _run(jobs, review)
    except Exception as exc:  # noqa: BLE001 — reported in the panel, not lost in a thread
        state.phase, state.error = "failed", str(exc)
        bus.publish(f"Search failed: {exc}", "error")
    finally:
        state.running = False
        state.finished_at = _now()
        _lock.release()


def _run(jobs: int, review: bool) -> None:
    global _process
    with db.connect() as conn:
        # The table's own clock and format, to compare created_at/closed_at with.
        since = conn.execute("SELECT datetime('now')").fetchone()[0]

    listed = subprocess.run([*_run_all(), "--list"], capture_output=True, text=True, timeout=60)
    state.sites_total = len(listed.stdout.split())
    bus.publish(f"Search started: {state.sites_total} career sites, {jobs} at a time.", "info")

    _process = subprocess.Popen(
        [*_run_all(), "--jobs", str(jobs)],
        cwd=paths.EXTRACTS_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    assert _process.stdout is not None
    for raw in _process.stdout:
        line = raw.strip()
        if not line:
            continue
        match = _SITE_LINE.match(line)
        if match:
            site = SiteResult(
                name=match["name"],
                status=match["status"],
                counts=match["counts"],
                new=int(match["new"]) if match["new"] else None,
                gone=int(match["gone"]) if match["gone"] else None,
                seconds=int(match["seconds"]),
            )
            state.sites.append(site)
            level = "success" if site.status == "OK" else "warn" if site.status != "FAILED" else "error"
            bus.publish(f"[{len(state.sites)}/{state.sites_total}] {line}", level)
        else:
            bus.publish(line, "info")
    _process.wait()
    _process = None
    if _stop.is_set():
        state.phase = "stopped"
        bus.publish("Search stopped; sites finished so far are imported.", "warn")

    if state.phase != "stopped":
        state.phase = "importing"
    extracts.sync()
    if state.phase != "stopped":
        state.phase = "ranking"
    ranking.refresh()
    _count_changes(since)
    bus.publish(
        f"{state.new_total} new postings, {state.gone_total} no longer listed "
        f"(across {sum(1 for c in state.changes if c.new or c.gone)} employers).",
        "success",
    )

    if review and not _stop.is_set():
        _review_new()
    if state.phase not in ("stopped", "failed"):
        state.phase = "done"


def _count_changes(since: str) -> None:
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT company,
                   SUM(created_at >= :since AND closed_at IS NULL) AS new,
                   SUM(closed_at >= :since) AS gone,
                   SUM(created_at >= :since AND closed_at IS NULL
                       AND COALESCE(priority_excluded, '') != '') AS new_excluded
            FROM jobs
            WHERE source_type = 'extract' AND (created_at >= :since OR closed_at >= :since)
            GROUP BY company
            ORDER BY new DESC, gone DESC, company
            """,
            {"since": since},
        ).fetchall()
    state.changes = [
        CompanyChange(r["company"], int(r["new"] or 0), int(r["gone"] or 0), int(r["new_excluded"] or 0))
        for r in rows
    ]
    state.new_total = sum(change.new for change in state.changes)
    state.gone_total = sum(change.gone for change in state.changes)


def _review_new() -> None:
    settings = config_module.load()
    if not settings.openrouter.api_key:
        bus.publish("No OpenRouter key in Settings: new postings keep their rules score.", "warn")
        return
    try:
        ranking.load_brief()
    except ranking.BriefMissing as exc:
        bus.publish(f"No review: {exc}", "warn")
        return
    state.phase = "reviewing"
    run = ranking.review_top(None, mode="new", say=lambda line: bus.publish(line, "info"))
    state.reviewed, state.review_cost = run.reviewed, run.cost
    if run.stopped:
        bus.publish(f"Review stopped: {run.stopped}", "warn")


def start_review(mode: str, limit: int | None, *, again: bool = False) -> bool:
    """Review on its own (after the brief changed), in the background.

    `again` reviews postings already reviewed against this brief too.
    """
    if is_running() or ranking.review_running():
        return False

    def work() -> None:
        try:
            ranking.review_top(limit, mode=mode, again=again, say=lambda line: bus.publish(line, "info"))
        except Exception as exc:  # noqa: BLE001
            bus.publish(f"Review failed: {exc}", "error")

    threading.Thread(target=work, name="review", daemon=True).start()
    return True

"""Background scheduling for the inspector and the alert daemon.

Deliberately a plain daemon thread rather than a task queue: a self-hosted
single-tenant portal should not need a broker to check a job board every few
hours.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

from . import config as config_module
from .logbus import bus
from .spider import runner

_thread: threading.Thread | None = None
_stop = threading.Event()
_next_run: datetime | None = None


def next_run_at() -> datetime | None:
    return _next_run


def is_active() -> bool:
    return _thread is not None and _thread.is_alive()


def _loop() -> None:
    global _next_run
    while not _stop.is_set():
        config = config_module.load(refresh=True)
        if not config.spider.enabled:
            _next_run = None
            # Poll the config periodically so enabling the spider in the UI
            # takes effect without restarting the process.
            _stop.wait(60)
            continue

        interval = max(15, config.spider.interval_minutes) * 60
        _next_run = datetime.now(UTC) + timedelta(seconds=interval)
        try:
            runner.run_once(config)
        except Exception as exc:  # pragma: no cover - keep the daemon alive
            bus.publish(f"Scheduled run failed: {exc}", "error")
        _stop.wait(interval)


def start() -> None:
    """Start the daemon thread if it is not already running."""
    global _thread
    if is_active():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="acide-scheduler", daemon=True)
    _thread.start()
    bus.publish("Scheduler thread started.", "info")


def stop(timeout: float = 5.0) -> None:
    global _thread
    _stop.set()
    if _thread is not None:
        _thread.join(timeout=timeout)
        _thread = None

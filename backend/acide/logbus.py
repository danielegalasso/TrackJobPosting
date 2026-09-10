"""In-memory log fan-out backing the spider's live console (SSE).

Bounded on both ends: a ring buffer for late subscribers, and per-subscriber
queues that drop the oldest line rather than let a slow browser tab hold the
inspector back.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Literal

Level = Literal["info", "warn", "error", "success"]

logger = logging.getLogger("acide.spider")

_HISTORY_LIMIT = 400
_QUEUE_LIMIT = 200


class LogBus:
    def __init__(self) -> None:
        self._history: deque[dict[str, str]] = deque(maxlen=_HISTORY_LIMIT)
        self._subscribers: set[asyncio.Queue[dict[str, str]]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Remember the serving loop so worker threads can publish into it."""
        self._loop = loop

    def publish(self, message: str, level: Level = "info") -> None:
        """Record a line and fan it out. Safe to call from any thread."""
        event = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "level": level,
            "message": message,
        }
        self._history.append(event)
        logger.log(
            {"info": logging.INFO, "success": logging.INFO,
             "warn": logging.WARNING, "error": logging.ERROR}[level],
            message,
        )
        if self._loop is None or self._loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            self._dispatch(event)
        else:
            self._loop.call_soon_threadsafe(self._dispatch, event)

    def _dispatch(self, event: dict[str, str]) -> None:
        for queue in list(self._subscribers):
            if queue.full():
                # Drop the oldest line rather than block the inspector.
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(event)

    def history(self) -> list[dict[str, str]]:
        return list(self._history)

    async def stream(self) -> AsyncIterator[dict[str, str]]:
        """Yield history, then every subsequent line, until cancelled."""
        queue: asyncio.Queue[dict[str, str]] = asyncio.Queue(maxsize=_QUEUE_LIMIT)
        self._subscribers.add(queue)
        try:
            for event in self.history():
                yield event
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)


bus = LogBus()

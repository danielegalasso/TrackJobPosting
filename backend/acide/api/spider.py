"""`/api/spider` — manual runs, status, and the live SSE log stream."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import StreamingResponse

from .. import config as config_module
from .. import scheduler
from ..logbus import bus
from ..models import SpiderRunSummary
from ..spider import runner

router = APIRouter(prefix="/api/spider", tags=["spider"])


@router.get("/status", response_model=dict)
def status() -> dict:
    config = config_module.load()
    next_run = scheduler.next_run_at()
    return {
        "running": runner.is_running(),
        "scheduler_active": scheduler.is_active(),
        "spider_enabled": config.spider.enabled,
        "interval_minutes": config.spider.interval_minutes,
        "next_run_at": next_run.isoformat() if next_run else None,
        "last_run": runner.current_state().model_dump(mode="json"),
        "targets": [target.model_dump() for target in config.targets],
    }


@router.post("/run", response_model=dict)
def trigger_run(background: BackgroundTasks, send_alerts: bool = True) -> dict:
    """Kick off an inspection pass without blocking the request."""
    if runner.is_running():
        return {"started": False, "detail": "a run is already in progress"}
    background.add_task(runner.run_once, None, send_alerts=send_alerts)
    return {"started": True, "detail": "inspection started"}


@router.get("/logs")
async def stream_logs() -> StreamingResponse:
    """Server-sent events carrying the inspector's console output."""

    async def event_source():
        try:
            async for event in bus.stream():
                yield f"data: {json.dumps(event)}\n\n"
        except asyncio.CancelledError:  # pragma: no cover - client disconnect
            raise

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # keep nginx from buffering the stream
        },
    )


@router.get("/logs/history", response_model=list[dict])
def log_history() -> list[dict]:
    return bus.history()


@router.get("/last-run", response_model=SpiderRunSummary)
def last_run() -> SpiderRunSummary:
    return runner.current_state()

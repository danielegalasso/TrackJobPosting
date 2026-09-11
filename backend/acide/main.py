"""ACIDE-Watch application entrypoint.

One process serves the JSON API, the SSE log stream, the legal documents
and the built single-page portal, so a self-hosted deployment is a single
`uvicorn` invocation.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, db, paths, scheduler
from . import config as config_module
from .api import alerts, jobs, spider
from .api import config as config_api
from .logbus import bus

logging.basicConfig(
    level=os.environ.get("ACIDE_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("acide")

# A self-hosted portal is same-origin by default; the dev server needs an
# explicit allowance, which stays opt-in via the environment.
DEV_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "ACIDE_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if origin.strip()
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    paths.ensure_dirs()
    db.init_db()
    bus.bind_loop(asyncio.get_running_loop())

    try:
        config = config_module.load(refresh=True)
    except ValueError as exc:
        logger.error("configuration problem: %s", exc)
        config = None

    # The loop polls setup.json on each tick, so it starts unconditionally:
    # enabling the inspector in the UI then takes effect without a restart.
    scheduler.start()
    if config and not config.spider.enabled:
        logger.info("scheduled inspection is disabled; manual runs still work")

    logger.info("ACIDE-Watch %s ready — data directory %s", __version__, paths.DATA_DIR)
    try:
        yield
    finally:
        scheduler.stop()


app = FastAPI(
    title="ACIDE-Watch Core Engine",
    version=__version__,
    description=(
        "Self-hosted job portal and automated scouting platform. Aggregates "
        "public employer career feeds and scores them on two independent "
        "fit vectors."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=DEV_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(jobs.router)
app.include_router(alerts.router)
app.include_router(config_api.router)
app.include_router(spider.router)


@app.get("/api/health", tags=["ops"])
def health() -> dict[str, object]:
    config = config_module.load()
    return {
        "status": "ok",
        "version": __version__,
        "openrouter_configured": bool(config.openrouter.api_key),
        "email_enabled": config.email.enabled,
        "spider_enabled": config.spider.enabled,
        "targets": len(config.targets),
    }


# --- Legal documents ---------------------------------------------------------
_LEGAL = {
    "privacy": paths.REPO_ROOT / "docs" / "PRIVACY.md",
    "terms": paths.REPO_ROOT / "docs" / "TERMS.md",
}


@app.get("/api/legal/{document}", tags=["legal"])
def legal(document: str) -> PlainTextResponse:
    """Serve the Privacy Policy / Terms so the modals show the real text."""
    path = _LEGAL.get(document)
    if path is None or not path.exists():
        return PlainTextResponse("Document not found.", status_code=404)
    return PlainTextResponse(path.read_text("utf-8"), media_type="text/markdown")


# --- Static portal -----------------------------------------------------------
def _warn_if_stale(dist: Path) -> None:
    """Say so when the served bundle predates the source it was built from.

    `frontend/dist` is not in git, so `git pull` updates the sources and
    leaves the built portal untouched. Without this, the browser silently
    shows the previous release and the new settings appear to be missing.
    """
    source = paths.REPO_ROOT / "frontend" / "src"
    if not source.is_dir():
        return
    try:
        newest_source = max(item.stat().st_mtime for item in source.rglob("*") if item.is_file())
        built_at = (dist / "index.html").stat().st_mtime
    except (OSError, ValueError):  # pragma: no cover - unreadable tree
        return
    if newest_source > built_at:
        logger.warning(
            "the built portal in %s is older than frontend/src — "
            "run `npm run build` in frontend/ to pick up the latest changes",
            dist,
        )


def _mount_frontend() -> None:
    dist: Path = paths.FRONTEND_DIST
    index = dist / "index.html"
    if not index.exists():
        logger.warning(
            "frontend build not found at %s — API only. Run `npm run build` in frontend/.",
            dist,
        )
        return
    _warn_if_stale(dist)

    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    # response_model=None: the return is a Response union, which FastAPI must
    # not try to turn into a response model.
    @app.get("/{full_path:path}", include_in_schema=False, response_model=None)
    def spa(full_path: str) -> FileResponse | JSONResponse:
        # Any unmatched API path is a genuine 404, not an SPA route.
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "not found"}, status_code=404)
        candidate = (dist / full_path).resolve()
        if full_path and candidate.is_file() and dist.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index)

    logger.info("serving portal from %s", dist)


_mount_frontend()

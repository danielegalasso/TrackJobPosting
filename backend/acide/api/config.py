"""`/api/config` — settings CRUD, CV upload and credential handshakes."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Body, File, HTTPException, Query, Response, UploadFile
from pydantic import ValidationError

from .. import config as config_module
from .. import db, resume
from ..llm import InferenceError, OpenRouterClient, filter_models
from ..mailer import MailError
from ..mailer import handshake as smtp_handshake
from ..models import HandshakeResult, ModelInfo, SetupConfig
from ..spider.base import CONNECTORS

router = APIRouter(prefix="/api/config", tags=["config"])

#: The catalogue is ~300 entries and changes on OpenRouter's schedule, not
#: ours, so it is fetched once and reused across keystrokes.
_MODEL_CACHE_TTL = 600.0
_model_cache: list[ModelInfo] | None = None
_model_cache_at = 0.0


@router.get("", response_model=dict)
def read_config() -> dict[str, Any]:
    """Current settings, with the API key and SMTP password masked."""
    data = config_module.redacted()
    active = resume.active_path()
    data["resume_filename"] = active.name if active else ""
    data["source_types"] = sorted(CONNECTORS)
    return data


@router.put("", response_model=dict)
def write_config(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Save settings. Masked secrets are preserved rather than overwritten."""
    payload.pop("source_types", None)
    merged = config_module.merge_secrets(payload)
    try:
        config = SetupConfig(**merged)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    config_module.save(config)
    return config_module.redacted(config)


@router.post("/resume", response_model=dict)
async def upload_resume(file: UploadFile = File(...)) -> dict[str, Any]:
    """Replace the active CV. The previous file is deleted, not archived."""
    payload = await file.read()
    try:
        stored = resume.store(file.filename or "cv", payload)
        text = resume.extract_text(stored)
    except resume.ResumeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    config = config_module.load()
    config.resume_filename = stored.name
    config_module.save(config)
    return {
        "filename": stored.name,
        "characters": len(text),
        "preview": text[:400],
    }


@router.delete("/resume", status_code=204, response_class=Response)
def delete_resume() -> Response:
    """Erase the stored CV immediately, as the Privacy Policy commits to."""
    resume.purge()
    config = config_module.load()
    config.resume_filename = ""
    config_module.save(config)
    return Response(status_code=204)


@router.get("/models", response_model=list[ModelInfo])
def list_models(
    q: str = Query("", description="Substring filter over model id and name"),
    refresh: bool = Query(False, description="Bypass the catalogue cache"),
) -> list[ModelInfo]:
    """OpenRouter's model catalogue, for the settings model picker.

    Cached in-process: the picker filters as the user types, and the
    catalogue changes far more slowly than that.
    """
    global _model_cache, _model_cache_at

    now = time.monotonic()
    if refresh or _model_cache is None or now - _model_cache_at > _MODEL_CACHE_TTL:
        config = config_module.load(refresh=True)
        try:
            with OpenRouterClient(config) as client:
                _model_cache = client.list_models()
                _model_cache_at = now
        except InferenceError as exc:
            # A stale catalogue beats an empty picker when the gateway blips.
            if _model_cache is None:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
    return filter_models(_model_cache, q)


@router.post("/test/openrouter", response_model=HandshakeResult)
def test_openrouter() -> HandshakeResult:
    """Send one real completion through the configured model and settings."""
    config = config_module.load(refresh=True)
    try:
        with OpenRouterClient(config) as client:
            return HandshakeResult(ok=True, detail=client.handshake())
    except InferenceError as exc:
        return HandshakeResult(ok=False, detail=str(exc))


@router.post("/test/smtp", response_model=HandshakeResult)
def test_smtp() -> HandshakeResult:
    config = config_module.load(refresh=True)
    try:
        return HandshakeResult(ok=True, detail=smtp_handshake(config.email))
    except MailError as exc:
        return HandshakeResult(ok=False, detail=str(exc))


@router.post("/purge-jobs", response_model=dict)
def purge_jobs() -> dict[str, int]:
    """Drop every indexed posting so the next run re-scores from scratch."""
    return {"deleted": db.delete_all_jobs()}

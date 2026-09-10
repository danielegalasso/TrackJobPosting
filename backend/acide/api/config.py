"""`/api/config` — settings CRUD, CV upload and credential handshakes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, File, HTTPException, Response, UploadFile
from pydantic import ValidationError

from .. import config as config_module
from .. import db, resume
from ..llm import InferenceError, OpenRouterClient
from ..mailer import MailError
from ..mailer import handshake as smtp_handshake
from ..models import HandshakeResult, SetupConfig
from ..spider.base import CONNECTORS

router = APIRouter(prefix="/api/config", tags=["config"])


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


@router.post("/test/openrouter", response_model=HandshakeResult)
def test_openrouter() -> HandshakeResult:
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

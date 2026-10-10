"""`/api/search` — run the extractors, and score postings against the brief."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .. import config as config_module
from .. import ranking, search
from ..models import REASONING_EFFORTS

router = APIRouter(prefix="/api/search", tags=["search"])

#: Tokens of a typical review, measured on 100 postings (brief ~1,200 of the
#: prompt): what a model's list price is multiplied by when there is no
#: history of what its reviews actually cost.
TYPICAL_PROMPT_TOKENS = 2500
TYPICAL_COMPLETION_TOKENS = 900


def _digest() -> str:
    try:
        return ranking.brief_digest(ranking.load_brief())
    except ranking.BriefMissing:
        return ""


def _scoring_settings() -> dict[str, Any]:
    settings = config_module.load().openrouter
    return {
        "model": settings.model,
        "reasoning_effort": settings.reasoning_effort,
        "max_concurrency": settings.max_concurrency,
        "has_key": bool(settings.api_key),
    }


@router.get("/status")
def status() -> dict[str, Any]:
    """Everything the Run the Search panel shows, in one poll."""
    return {
        "search": search.state.as_dict(),
        "review": ranking.review_state.__dict__,
        "counts": ranking.review_counts(_digest()),
        "scoring": _scoring_settings(),
        "brief_present": bool(_digest()),
    }


@router.post("/run")
def run(jobs: int = Query(search.DEFAULT_JOBS, ge=1, le=16), review: bool = True) -> dict[str, Any]:
    if not search.start(jobs=jobs, review=review):
        raise HTTPException(status_code=409, detail="a search or a review is already running")
    return {"started": True}


@router.post("/stop")
def stop() -> dict[str, Any]:
    search.stop()
    return {"stopping": True}


class BriefBody(BaseModel):
    text: str = Field(min_length=1)


@router.get("/brief")
def get_brief() -> dict[str, Any]:
    try:
        text = ranking.load_brief()
    except ranking.BriefMissing:
        text = ""
    return {"text": text, "instructions": ranking.REVIEW_INSTRUCTIONS.strip(), "digest": _digest()}


@router.put("/brief")
def put_brief(body: BriefBody) -> dict[str, Any]:
    """Save the brief. Reviews made against the old one become stale; they
    are scored again only when asked (POST /review with mode=stale)."""
    if ranking.review_running():
        raise HTTPException(status_code=409, detail="a review is running; stop it before changing the brief")
    try:
        digest = ranking.save_brief(body.text)
    except ranking.BriefMissing as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"digest": digest, "counts": ranking.review_counts(digest)}


class ScoringBody(BaseModel):
    model: str = Field(min_length=1)
    reasoning_effort: str
    max_concurrency: int = Field(ge=1, le=32)


@router.put("/scoring")
def put_scoring(body: ScoringBody) -> dict[str, Any]:
    """The model, reasoning effort and parallel calls reviews are made with."""
    if body.reasoning_effort not in REASONING_EFFORTS:
        raise HTTPException(status_code=422, detail=f"effort must be one of {', '.join(REASONING_EFFORTS)}")
    config = config_module.load(refresh=True)
    config.openrouter.model = body.model.strip()
    config.openrouter.reasoning_effort = body.reasoning_effort
    config.openrouter.max_concurrency = body.max_concurrency
    config_module.save(config)
    return _scoring_settings()


def _list_price(model: str) -> float | None:
    """A typical review's cost at the model's list price, or None if unknown."""
    from .config import list_models

    try:
        catalogue = list_models(q="", refresh=False)
    except HTTPException:
        return None
    for entry in catalogue:
        if entry.id == model and entry.prompt_price is not None and entry.completion_price is not None:
            return (
                entry.prompt_price * TYPICAL_PROMPT_TOKENS
                + entry.completion_price * TYPICAL_COMPLETION_TOKENS
            )
    return None


@router.get("/estimate")
def estimate(
    mode: str = Query("stale", pattern="^(new|stale|all)$"),
    limit: int | None = Query(None, ge=1),
) -> dict[str, Any]:
    """How many reviews a run would make, and what they would cost."""
    digest = _digest()
    if not digest:
        raise HTTPException(status_code=409, detail="write the brief first")
    # The review ranks what is pending before choosing; so must the count,
    # or postings imported but not yet ranked are left out of it.
    ranking.refresh()
    count = len(
        ranking.candidates_for_review(limit, digest, again=mode == "all", mode="new" if mode == "new" else "stale")
    )
    model = config_module.load().openrouter.model
    per_posting, samples = ranking.average_cost(model)
    source = "history"
    if per_posting is None or samples < 5:
        listed = _list_price(model)
        if listed is not None:
            per_posting, source = listed, "list price"
        elif per_posting is None:
            source = "unknown"
    return {
        "count": count,
        "model": model,
        "per_posting": per_posting,
        "source": source,
        "samples": samples,
        "total": per_posting * count if per_posting is not None else None,
    }


class ReviewBody(BaseModel):
    mode: str = Field(pattern="^(new|stale|all)$")
    limit: int | None = Field(default=None, ge=1)


@router.post("/review")
def review(body: ReviewBody) -> dict[str, Any]:
    """Score postings against the brief in the background: the new ones, the
    stale ones (after the brief changed), or all of them again."""
    if not _digest():
        raise HTTPException(status_code=409, detail="write the brief first")
    if not config_module.load().openrouter.api_key:
        raise HTTPException(status_code=409, detail="add an OpenRouter key in Settings first")
    mode = "new" if body.mode == "new" else "stale"
    if not search.start_review(mode, body.limit, again=body.mode == "all"):
        raise HTTPException(status_code=409, detail="a search or a review is already running")
    return {"started": True}


@router.post("/review/stop")
def stop_review() -> dict[str, Any]:
    ranking.stop_review()
    return {"stopping": True}

"""`/api/jobs` — the query surface behind the filter matrix and results grid."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from .. import db
from ..models import Job, JobFacets, JobPage

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("", response_model=JobPage)
def list_jobs(
    search: str | None = Query(None, description="Free text over title, company, category"),
    location: str | None = None,
    remote: str | None = Query(None, description="Remote-from region"),
    category: str | None = None,
    seniority: str | None = None,
    company: str | None = None,
    years_experience: str | None = Query(None, pattern="^(0-2|3-5|6-9|10\\+)$"),
    posted_within: str | None = Query(None, pattern="^(today|3days|week|month)$"),
    category_type: str | None = None,
    rate: str | None = None,
    currency: str | None = None,
    min_amount: float | None = Query(None, ge=0),
    match_posting_currency: bool = False,
    saved_only: bool = False,
    include_dismissed: bool = False,
    sort: str = Query("fit", pattern="^(fit|recent|experience|interest|compensation)$"),
    limit: int = Query(60, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> JobPage:
    return db.list_jobs(
        limit=limit,
        offset=offset,
        sort=sort,
        search=search,
        location=location,
        remote=remote,
        category=category,
        seniority=seniority,
        company=company,
        years_experience=years_experience,
        posted_within=posted_within,
        category_type=category_type,
        rate=rate,
        currency=currency,
        min_amount=min_amount,
        match_posting_currency=match_posting_currency,
        saved_only=saved_only,
        include_dismissed=include_dismissed,
    )


@router.get("/facets", response_model=JobFacets)
def job_facets() -> JobFacets:
    """Distinct values powering the Category / Company / Seniority dropdowns."""
    return db.facets()


@router.get("/{job_id:path}", response_model=Job)
def get_job(job_id: str) -> Job:
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.post("/{job_id:path}/bookmark", response_model=Job)
def toggle_bookmark(job_id: str, saved: bool | None = None) -> Job:
    """Toggle the bookmark, or set it explicitly with `?saved=true|false`."""
    job = db.set_job_flag(job_id, "saved", saved)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.post("/{job_id:path}/dismiss", response_model=Job)
def toggle_dismiss(job_id: str, dismissed: bool | None = None) -> Job:
    job = db.set_job_flag(job_id, "dismissed", dismissed)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job

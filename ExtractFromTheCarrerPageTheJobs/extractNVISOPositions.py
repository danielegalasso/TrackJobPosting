#!/usr/bin/env python3
"""Every NVISO posting on its own careers page.

    https://www.nviso.eu/jobs

NVISO runs no ATS page of its own: the site is Next.js over WordPress, and
each page carries the GraphQL results it was rendered from in its
__NEXT_DATA__ script, under props.pageProps.urqlState. The jobs page holds
the whole list (no pagination) and the offices it refers to; each
/job/<slug> page holds that posting's advert as `job.content`.

What is handled here:
  * The list query carries no total, so the run checks itself against the
    /job/ links the page renders — what a visitor sees and counts.
  * A posting names its office by id; the city and country come from the
    jobs page's own offices query.
  * WordPress categories mix location, experience level and team, under
    several overlapping taxonomies ("CSIRT/SOC" appears three times) and with
    their parent terms ("Job Location", "Team") attached too. Location comes
    from the office, experience from /job-experience/, and team is whatever
    is left, deduplicated.

Output and the completeness check are in extract_common.py.
Usage:  python3 extractNVISOPositions.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from extract_common import DETAIL_WORKERS, finish_run, html_to_text, request_text

NAME = "NVISO"
SITE = "https://www.nviso.eu"
LIST_URL = f"{SITE}/jobs"
OUT_DIR = Path(__file__).resolve().parent / "NVISO"

_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)
_JOB_LINK_RE = re.compile(r'href="(?:https://www\.nviso\.eu)?/job/([^"/?#]+)/?"')
#: Category terms that only group others.
_PARENT_TERMS = {"Job Location", "Job Experience", "Team", "Job Team"}


def page_queries(html: str) -> dict[str, Any]:
    """The GraphQL results a page was rendered from, keyed by their root field."""
    match = _NEXT_DATA_RE.search(html)
    if not match:
        raise RuntimeError("no __NEXT_DATA__ on the page; has the site changed?")
    state = json.loads(match.group(1))["props"]["pageProps"].get("urqlState") or {}
    queries: dict[str, Any] = {}
    for entry in state.values():
        data = entry.get("data")
        data = json.loads(data) if isinstance(data, str) else data
        for root, value in (data or {}).items():
            queries.setdefault(root, value)
    return queries


def fetch_content(slug: str) -> str:
    try:
        job = page_queries(request_text(f"{SITE}/job/{slug}/")).get("job") or {}
    except RuntimeError as exc:
        print(f"  ! no detail for {slug}: {exc}", file=sys.stderr)
        return ""
    return str(job.get("content") or "")


def _categories(job: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        (str(node.get("name") or ""), str(node.get("uri") or ""))
        for node in (job.get("categories") or {}).get("nodes") or []
    ]


def build_record(job: dict[str, Any], offices: dict[int, dict[str, Any]], content: str) -> dict[str, Any]:
    fields = job.get("cptJobFields") or {}
    places = []
    for node in (fields.get("office") or {}).get("nodes") or []:
        office = offices.get(node.get("databaseId")) or {}
        countries = (office.get("cptOfficeCustomFields") or node.get("cptOfficeCustomFields") or {}).get(
            "country"
        ) or []
        places.append((office.get("title") or "", countries[0] if countries else ""))

    categories = _categories(job)
    experience = [name for name, uri in categories if "/job-experience/" in uri and name not in _PARENT_TERMS]
    team: list[str] = []
    for name, uri in categories:
        if "/location/" in uri or "/job-experience/" in uri or name in _PARENT_TERMS or name in team:
            continue
        team.append(name)

    slug = str(job.get("slug") or "")
    return {
        "job_id": str(job.get("databaseId") or slug),
        "title": str(job.get("title") or "").strip(),
        "location": ", ".join(part for part in places[0] if part) if places else "",
        "additional_locations": "; ".join(", ".join(p for p in place if p) for place in places[1:]),
        "country": places[0][1] if places else "",
        "remote_type": "Remote" if fields.get("isRemote") else "",
        "experience": "; ".join(dict.fromkeys(experience)),
        "team": "; ".join(team),
        "url": f"{SITE}{job.get('uri') or f'/job/{slug}/'}",
        "description": html_to_text(content or str(job.get("excerpt") or "")),
    }


def main() -> int:
    argparse.ArgumentParser(description=f"Extract every {NAME} posting on its careers page.").parse_args()

    started = datetime.now()
    print(f"{NAME} — every posting on {LIST_URL}")
    html = request_text(LIST_URL)
    queries = page_queries(html)
    jobs = {str(job.get("slug")): job for job in (queries.get("jobs") or {}).get("nodes") or []}
    offices = {
        office.get("databaseId"): office for office in (queries.get("offices") or {}).get("nodes") or []
    }
    linked = set(_JOB_LINK_RE.findall(html))
    print(f"  {len(jobs)} postings in the page data, {len(linked)} linked on the page")
    unlisted = linked - jobs.keys()
    if unlisted:
        print(f"  ! linked but not in the data: {', '.join(sorted(unlisted))}", file=sys.stderr)

    print(f"  fetching {len(jobs)} adverts…")
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        contents = list(pool.map(fetch_content, jobs))

    return finish_run(
        name=NAME,
        out_dir=OUT_DIR,
        # The page has no search; the whole board is the result.
        query="",
        source=LIST_URL,
        started=started,
        total=max(len(linked), len(jobs)),
        records=[build_record(job, offices, content) for job, content in zip(jobs.values(), contents)],
        missing_detail=sum(1 for content in contents if not content),
    )


if __name__ == "__main__":
    sys.exit(main())

"""Extract every posting on a Greenhouse job board.

Many companies show Greenhouse postings on their own careers page, and that
page can be out of reach of a script: https://helsing.ai/jobs answers 429
with a Vercel Security Checkpoint, a JavaScript challenge meant to keep bots
out. Greenhouse publishes the same board through its documented public API,

    GET https://boards-api.greenhouse.io/v1/boards/<board>/jobs?content=true

which returns every posting, advert included, in one response, with
`meta.total` alongside. The board name is found by trying the company name
there; a hit is confirmed when the postings' `absolute_url` points back at
the company's own careers page.

What is handled here:
  * The advert in `content` is HTML escaped a second time ("&lt;h2&gt;"), so
    it is unescaped before being turned into text.
  * One role is often published several times, typically once per location
    (Helsing: 166 postings of 145 jobs). Each is its own posting with its own
    URL, so postings are told apart by id, not by internal job.

Output and the completeness check are in extract_common.py. Each company has
a small entry script that names its board and calls `main`.
"""

from __future__ import annotations

import argparse
import html
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from extract_common import finish_run, html_to_text, request_json


@dataclass(frozen=True)
class Board:
    """One Greenhouse job board."""

    name: str
    board: str
    #: The company's own careers page, for the record of where postings show.
    careers_url: str
    out_dir: Path

    @property
    def jobs_url(self) -> str:
        return f"https://boards-api.greenhouse.io/v1/boards/{self.board}/jobs?content=true"


def _names(items: list[dict[str, Any]] | None) -> list[str]:
    return list(dict.fromkeys(str(item.get("name") or "") for item in items or [] if item.get("name")))


def build_record(job: dict[str, Any]) -> dict[str, Any]:
    offices = job.get("offices") or []
    # An office's `location` reads "München, Germany"; the country is last.
    countries = [
        str(office.get("location") or "").rsplit(",", 1)[-1].strip()
        for office in offices
        if office.get("location")
    ]
    return {
        "job_id": str(job.get("id") or ""),
        "requisition_id": str(job.get("requisition_id") or ""),
        "title": str(job.get("title") or "").strip(),
        "location": str((job.get("location") or {}).get("name") or ""),
        "offices": "; ".join(_names(offices)),
        "country": "; ".join(dict.fromkeys(country for country in countries if country)),
        "department": "; ".join(_names(job.get("departments"))),
        "start_date": str(job.get("first_published") or "")[:10],
        "updated": str(job.get("updated_at") or "")[:10],
        "deadline": str(job.get("application_deadline") or "")[:10],
        "url": str(job.get("absolute_url") or ""),
        "description": html_to_text(html.unescape(str(job.get("content") or ""))),
    }


def main(board: Board) -> int:
    argparse.ArgumentParser(description=f"Extract every {board.name} posting on its Greenhouse board.").parse_args()

    started = datetime.now()
    print(f"{board.name} Greenhouse — every posting on board '{board.board}'")
    payload = request_json(board.jobs_url)
    jobs = {str(job.get("id")): job for job in payload.get("jobs") or []}
    total = int((payload.get("meta") or {}).get("total") or len(jobs))
    internal = {job.get("internal_job_id") for job in jobs.values()}
    print(f"  {len(jobs)} postings ({len(internal)} distinct jobs), board reports {total}")

    records = [build_record(job) for job in jobs.values()]
    return finish_run(
        name=board.name,
        out_dir=board.out_dir,
        # The board is taken whole; there is no search.
        query="",
        source=board.careers_url,
        started=started,
        total=total,
        records=records,
        missing_detail=sum(1 for record in records if not record["description"]),
    )

"""Extract every posting that a search on a Workday career site returns.

A Workday careers page such as
    https://ag.wd3.myworkdayjobs.com/en-US/Airbus?q=cyber%20security
shows 20 postings at a time behind arrow buttons. The page itself is empty
HTML: what the arrows do is POST to Workday's own JSON endpoint

    POST https://<host>/wday/cxs/<tenant>/<site>/jobs
         {"appliedFacets": {}, "limit": 20, "offset": N, "searchText": "..."}

so this calls that endpoint directly, one page per arrow click, and then
fetches each posting's own document for the advert text:

    GET  https://<host>/wday/cxs/<tenant>/<site><externalPath>

Workday traps handled here:
  * `limit` above 20 returns an empty page with no error, so pages are 20.
  * `total` is only filled in on the first page; later pages say 0.
  * The list is ordered by relevance and can shift while it is being walked,
    so a single pass may see a posting twice and miss another. The walk is
    repeated until the number of distinct postings matches `total`.
  * One requisition can be published more than once (Leonardo's paths end in
    `_R0033251-1`), so postings are told apart by their path, not their
    requisition number.

Output and the completeness check are in extract_common.py. Each company has
a small entry script that names its board and calls `main`.
"""

from __future__ import annotations

import argparse
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from extract_common import DETAIL_WORKERS, finish_run, html_to_text, request_json

#: Workday silently returns an empty page for anything larger.
PAGE_SIZE = 20
#: Full walks of the listing before giving up on reaching `total`.
MAX_WALKS = 4


@dataclass(frozen=True)
class Board:
    """One Workday career site: what its CXS URLs are built from."""

    name: str
    host: str
    tenant: str
    site: str
    #: Locale segment of the public page, only used to build links.
    locale: str
    default_query: str
    out_dir: Path

    @property
    def jobs_url(self) -> str:
        return f"https://{self.host}/wday/cxs/{self.tenant}/{self.site}/jobs"

    def detail_url(self, path: str) -> str:
        return f"https://{self.host}/wday/cxs/{self.tenant}/{self.site}{path}"

    def public_url(self, path: str = "") -> str:
        return f"https://{self.host}/{self.locale}/{self.site}{path}"


def _job_id(entry: dict[str, Any]) -> str:
    """The requisition number (JR…, R…), which Workday puts in bulletFields."""
    bullets = entry.get("bulletFields") or []
    if bullets:
        return str(bullets[0])
    return str(entry.get("externalPath", "")).rsplit("_", 1)[-1]


def list_postings(
    board: Board, query: str, facets: dict[str, list[str]] | None = None
) -> tuple[dict[str, dict[str, Any]], int]:
    """Every listing entry for `query`, keyed by posting path, and the site's total.

    `facets` are the page's filters as Workday names them, e.g.
    {"locations": ["2dc6ef…"]} for a link carrying ?locations=2dc6ef….
    """
    found: dict[str, dict[str, Any]] = {}
    total = 0
    for walk in range(1, MAX_WALKS + 1):
        offset = 0
        while True:
            payload = request_json(
                board.jobs_url,
                {
                    "appliedFacets": facets or {},
                    "limit": PAGE_SIZE,
                    "offset": offset,
                    "searchText": query,
                },
            )
            if offset == 0 and payload.get("total"):
                # Only the first page carries it; keep the latest figure.
                total = int(payload["total"])
            entries = payload.get("jobPostings") or []
            for entry in entries:
                found.setdefault(entry.get("externalPath") or _job_id(entry), entry)
            offset += len(entries)
            if len(entries) < PAGE_SIZE or (total and offset >= total):
                break
        print(f"  walk {walk}: {len(found)} distinct postings of {total} reported")
        if len(found) >= total:
            break
    return found, total


def fetch_detail(board: Board, path: str) -> dict[str, Any]:
    try:
        payload = request_json(board.detail_url(path))
    except RuntimeError as exc:
        print(f"  ! no detail for {path}: {exc}", file=sys.stderr)
        return {}
    info = dict(payload.get("jobPostingInfo") or {})
    info["_legal_entity"] = (payload.get("hiringOrganization") or {}).get("name", "")
    return info


def build_record(board: Board, path: str, entry: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": info.get("jobReqId") or _job_id(entry),
        "title": (info.get("title") or entry.get("title") or "").strip(),
        # The listing says "2 Locations" for multi-site roles; the detail
        # document names the primary one.
        "location": info.get("location") or entry.get("locationsText") or "",
        "additional_locations": "; ".join(info.get("additionalLocations") or []),
        "country": (info.get("country") or {}).get("descriptor", ""),
        "time_type": info.get("timeType", ""),
        "start_date": info.get("startDate", ""),
        "posted_on": info.get("postedOn") or entry.get("postedOn", ""),
        "legal_entity": info.get("_legal_entity", ""),
        "url": info.get("externalUrl") or board.public_url(path),
        "description": html_to_text(info.get("jobDescription") or ""),
    }


def main(board: Board) -> int:
    parser = argparse.ArgumentParser(description=f"Extract every {board.name} posting a Workday search returns.")
    parser.add_argument(
        "--query", default=board.default_query, help=f'search text (default "{board.default_query}")'
    )
    args = parser.parse_args()

    started = datetime.now()
    print(f'{board.name} Workday — searching "{args.query}"')
    listing, total = list_postings(board, args.query)

    print(f"  fetching {len(listing)} adverts…")
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        details = list(pool.map(lambda path: fetch_detail(board, path), listing))

    return finish_run(
        name=board.name,
        out_dir=board.out_dir,
        query=args.query,
        source=f"{board.public_url()}?q={urllib.parse.quote(args.query)}",
        started=started,
        total=total,
        records=[
            build_record(board, path, entry, info)
            for (path, entry), info in zip(listing.items(), details)
        ],
        missing_detail=sum(1 for info in details if not info),
    )

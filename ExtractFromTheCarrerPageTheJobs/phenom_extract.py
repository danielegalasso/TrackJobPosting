"""Extract every posting that a search on a Phenom People career site returns.

Phenom sites (careers.thalesgroup.com and others; the tell is a
`/search-results?keywords=` URL and assets on cdn.phenompeople.com) render
their listing from one JSON endpoint on the site's own host:

    POST https://<host>/widgets
         {"ddoKey": "refineSearch", "keywords": "...", "from": N, "size": 500, ...}
    POST https://<host>/widgets
         {"ddoKey": "jobDetail", "jobSeqNo": "..."}

The first returns a page of the search plus `totalHits`, the number the site
shows above its results; the second returns one posting with its advert.
Neither needs a session cookie.

Phenom traps handled here:
  * The results are ordered by relevance, and that order is not stable from
    one request to the next. Walking 2,324 Thales results 10 at a time, as
    the page's own arrows do, saw 70 postings twice and never saw 70 others.
    Pages are therefore as large as Phenom allows (500, which it caps
    silently), leaving five page boundaries instead of 233, and the walk is
    repeated until the number of distinct postings matches `totalHits`.
  * Sorting by date does not help: ties reshuffle the same way.

Output and the completeness check are in extract_common.py. Each company has
a small entry script that names its site and calls `main`.
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from extract_common import DETAIL_WORKERS, finish_run, html_to_text, request_json

#: Phenom returns at most this many per request, whatever `size` asks for.
PAGE_SIZE = 500
#: Full walks of the listing before giving up on reaching `totalHits`.
MAX_WALKS = 6


@dataclass(frozen=True)
class Site:
    """One Phenom career site, as its own pages address the widgets endpoint."""

    name: str
    host: str
    #: The path segments in front of `/search-results`, e.g. global/en.
    country: str
    language: str
    #: Phenom's locale string, as the page's phApp.ddo carries it.
    locale: str
    default_query: str
    out_dir: Path

    @property
    def widgets_url(self) -> str:
        return f"https://{self.host}/widgets"

    def public_url(self, path: str = "") -> str:
        return f"https://{self.host}/{self.country}/{self.language}/{path}"

    def job_url(self, job_id: str, title: str) -> str:
        slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-")
        return self.public_url(f"job/{job_id}/{slug}")


def _search_page(site: Site, query: str, offset: int) -> dict[str, Any]:
    payload = request_json(
        site.widgets_url,
        {
            "lang": site.locale,
            "deviceType": "desktop",
            "country": site.country,
            "pageName": "search-results",
            "ddoKey": "refineSearch",
            "sortBy": "",
            "subsearch": "",
            "from": offset,
            "jobs": True,
            "counts": False,
            "all_fields": [],
            "size": PAGE_SIZE,
            "clearAll": False,
            "jdsource": "facets",
            "isSliderEnable": False,
            "siteType": "external",
            "keywords": query,
            "global": True,
            "selected_fields": {},
            "locationData": {},
        },
    )
    return payload.get("refineSearch") or {}


def list_postings(site: Site, query: str) -> tuple[dict[str, dict[str, Any]], int]:
    """Every listing entry for `query`, keyed by jobSeqNo, and the site's total."""
    found: dict[str, dict[str, Any]] = {}
    total = 0
    for walk in range(1, MAX_WALKS + 1):
        offset = 0
        while True:
            result = _search_page(site, query, offset)
            total = int(result.get("totalHits") or total)
            entries = (result.get("data") or {}).get("jobs") or []
            for entry in entries:
                found.setdefault(entry.get("jobSeqNo") or entry.get("jobId"), entry)
            offset += len(entries)
            if not entries or offset >= total:
                break
        print(f"  walk {walk}: {len(found)} distinct postings of {total} reported")
        if len(found) >= total:
            break
    return found, total


def fetch_detail(site: Site, seq_no: str) -> dict[str, Any]:
    try:
        payload = request_json(
            site.widgets_url,
            {
                "lang": site.locale,
                "deviceType": "desktop",
                "country": site.country,
                "pageName": "job",
                "ddoKey": "jobDetail",
                "jobSeqNo": seq_no,
                "siteType": "external",
            },
        )
    except RuntimeError as exc:
        print(f"  ! no detail for {seq_no}: {exc}", file=sys.stderr)
        return {}
    job = ((payload.get("jobDetail") or {}).get("data") or {}).get("job")
    return job if isinstance(job, dict) else {}


def _place(location: dict[str, Any]) -> str:
    parts = [location.get("cityState") or location.get("city"), location.get("country")]
    return ", ".join(part for part in parts if part)


def build_record(site: Site, entry: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    merged = {**entry, **job}
    job_id = str(merged.get("jobId") or merged.get("reqId") or "")
    title = str(merged.get("title") or "").strip()
    places = merged.get("multi_location") or []
    extra = [_place(place) for place in places[1:] if isinstance(place, dict)]
    return {
        "job_id": job_id,
        "title": title,
        "location": merged.get("cityStateCountry") or merged.get("location") or "",
        "additional_locations": "; ".join(place for place in extra if place),
        "country": merged.get("country", ""),
        "time_type": merged.get("type") or merged.get("hiringType") or "",
        "remote_type": merged.get("remoteType", ""),
        "start_date": str(merged.get("postedDate") or "")[:10],
        "legal_entity": merged.get("companyValue") or merged.get("companyName") or "",
        "business_unit": merged.get("businessUnit", ""),
        "category": merged.get("category", ""),
        "url": site.job_url(job_id, title),
        "apply_url": merged.get("applyUrl", ""),
        "description": html_to_text(str(merged.get("description") or "")),
    }


def main(site: Site) -> int:
    parser = argparse.ArgumentParser(description=f"Extract every {site.name} posting a Phenom search returns.")
    parser.add_argument(
        "--query", default=site.default_query, help=f'search text (default "{site.default_query}")'
    )
    args = parser.parse_args()

    started = datetime.now()
    print(f'{site.name} Phenom — searching "{args.query}"')
    listing, total = list_postings(site, args.query)

    print(f"  fetching {len(listing)} adverts…")
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        details = list(pool.map(lambda seq_no: fetch_detail(site, seq_no), listing))

    return finish_run(
        name=site.name,
        out_dir=site.out_dir,
        query=args.query,
        source=site.public_url(f"search-results?keywords={urllib.parse.quote(args.query)}"),
        started=started,
        total=total,
        records=[build_record(site, entry, job) for entry, job in zip(listing.values(), details)],
        missing_detail=sum(1 for job in details if not job.get("description")),
    )

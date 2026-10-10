"""Extract every posting on an SAP SuccessFactors career site.

SuccessFactors Recruiting Marketing ("RMK") sites such as
    https://jobs.genua.de/search?facetFilters=%7B%7D&pageNumber=0
render no postings in their HTML. The page's script asks

    POST https://<host>/services/recruiting/v1/jobs
         {"locale": "de_DE", "pageNumber": N, "keywords": "", "facetFilters": {}, ...}

with the CSRF token the search page hands out (`var CSRFToken = "..."`) in an
X-CSRF-Token header, and gets back 10 postings plus `totalJobs`. Each
posting's advert is only on its own HTML page,

    GET  https://<host>/job/<urlTitle>/<id>-<locale>/

inside `<span itemprop="description">`, with an "at a glance" box of
label/value pairs (Einstiegslevel, Bereich, Standort, …) beside it.

What is handled here:
  * The token is read from the search page in the same session, since it is
    tied to the cookie that page sets.
  * A page past the end has no `jobSearchResult` key rather than an empty one.
  * The advert span holds spans of its own, so it is cut out by an HTML
    parser that counts nesting, not by a regular expression.
  * The walk is repeated until the number of distinct postings matches
    `totalJobs`, as on the other platforms.

Output and the completeness check are in extract_common.py. Each company has
a small entry script that names its site and calls `main`.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from extract_common import DETAIL_WORKERS, finish_run, html_to_text, inner_html, request_json, request_text

#: Full walks of the listing before giving up on reaching `totalJobs`.
MAX_WALKS = 4
#: Safety stop for a listing that never runs out.
MAX_PAGES = 500

_CSRF_RE = re.compile(r'CSRFToken\s*=\s*"([^"]+)"')
_FIELD_RE = re.compile(
    r'class="joblayouttoken-label"[^>]*>\s*([^<]+?)\s*:?\s*</span>\s*<span[^>]*>\s*([^<]*?)\s*</span>'
)


@dataclass(frozen=True)
class Site:
    """One SuccessFactors career site."""

    name: str
    host: str
    locale: str
    default_query: str
    out_dir: Path
    #: The site's labels in its "at a glance" box, mapped to output columns.
    fields: tuple[tuple[str, str], ...] = ()

    @property
    def jobs_url(self) -> str:
        return f"https://{self.host}/services/recruiting/v1/jobs"

    def job_url(self, url_title: str, job_id: str) -> str:
        # urlTitle comes percent-encoded but with HTML entities left in
        # ("NATO-&amp;-EU"); the id is what the site routes on.
        slug = urllib.parse.quote(html.unescape(url_title), safe="%()-")
        return f"https://{self.host}/job/{slug}/{job_id}-{self.locale}/"


def csrf_token(site: Site) -> str:
    page = request_text(f"https://{site.host}/search?locale={site.locale}")
    match = _CSRF_RE.search(page)
    if not match:
        raise RuntimeError(f"no CSRFToken on https://{site.host}/search; has the site changed?")
    return match.group(1)


def list_postings(site: Site, query: str) -> tuple[dict[str, dict[str, Any]], int]:
    """Every listing entry for `query`, keyed by job id, and the site's total."""
    headers = {"X-CSRF-Token": csrf_token(site)}
    found: dict[str, dict[str, Any]] = {}
    total = 0
    for walk in range(1, MAX_WALKS + 1):
        seen = 0
        for page in range(MAX_PAGES):
            payload = request_json(
                site.jobs_url,
                {
                    "locale": site.locale,
                    "pageNumber": page,
                    "sortBy": "",
                    "keywords": query,
                    "location": "",
                    "facetFilters": {},
                    "brand": "",
                    "skills": [],
                    "categoryId": 0,
                    "alertId": "",
                    "rcmCandidateId": "",
                },
                headers=headers,
            )
            total = int(payload.get("totalJobs") or total)
            entries = [item.get("response") or {} for item in payload.get("jobSearchResult") or []]
            for entry in entries:
                found.setdefault(str(entry.get("id")), entry)
            seen += len(entries)
            if not entries or seen >= total:
                break
        print(f"  walk {walk}: {len(found)} distinct postings of {total} reported")
        if len(found) >= total:
            break
    return found, total


def fetch_detail(site: Site, url: str) -> dict[str, str]:
    """The advert and the "at a glance" label/value pairs of one posting."""
    try:
        page = request_text(url)
    except RuntimeError as exc:
        print(f"  ! no detail for {url}: {exc}", file=sys.stderr)
        return {}
    detail = {
        html.unescape(label).strip(): html.unescape(value).strip()
        for label, value in _FIELD_RE.findall(page)
    }
    detail["_description"] = next(iter(inner_html(page, "itemprop", "description")), "")
    return detail


def _date(raw: str) -> str:
    """SuccessFactors prints the start date in the locale's short form."""
    for pattern in ("%d.%m.%y", "%m/%d/%y", "%d/%m/%y"):
        try:
            return datetime.strptime(raw, pattern).date().isoformat()
        except ValueError:
            continue
    return raw


def build_record(site: Site, entry: dict[str, Any], detail: dict[str, str]) -> dict[str, Any]:
    job_id = str(entry.get("id") or "")
    places = [
        re.sub(r"<br\s*/?>", "", html.unescape(place)).strip(" ,")
        for place in entry.get("jobLocationShort") or []
    ]
    record: dict[str, Any] = {
        "job_id": job_id,
        "title": html.unescape(str(entry.get("unifiedStandardTitle") or "")).strip(),
        "location": places[0] if places else "",
        "additional_locations": "; ".join(places[1:]),
        "start_date": _date(str(entry.get("unifiedStandardStart") or "")),
    }
    for label, column in site.fields:
        record[column] = detail.get(label, "")
    record["url"] = site.job_url(str(entry.get("urlTitle") or ""), job_id)
    record["description"] = html_to_text(detail.get("_description", ""))
    return record


def main(site: Site) -> int:
    parser = argparse.ArgumentParser(description=f"Extract every {site.name} posting on its SuccessFactors site.")
    parser.add_argument(
        "--query", default=site.default_query, help=f'search text (default "{site.default_query or "every posting"}")'
    )
    args = parser.parse_args()

    started = datetime.now()
    searched = f'searching "{args.query}"' if args.query else "every posting"
    print(f"{site.name} SuccessFactors — {searched}")
    listing, total = list_postings(site, args.query)

    urls = [site.job_url(str(entry.get("urlTitle") or ""), job_id) for job_id, entry in listing.items()]
    print(f"  fetching {len(urls)} adverts…")
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        details = list(pool.map(lambda url: fetch_detail(site, url), urls))

    query_string = urllib.parse.urlencode({"keywords": args.query} if args.query else {"facetFilters": "{}"})
    return finish_run(
        name=site.name,
        out_dir=site.out_dir,
        query=args.query,
        source=f"https://{site.host}/search?{query_string}",
        started=started,
        total=total,
        records=[build_record(site, entry, detail) for entry, detail in zip(listing.values(), details)],
        missing_detail=sum(1 for detail in details if not detail.get("_description")),
    )

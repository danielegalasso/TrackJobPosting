#!/usr/bin/env python3
"""Every Aerospacelab posting on its own careers page.

    https://www.aerospacelab.com/jobs

The site is Odoo's website recruitment module under a custom theme (the
`x_wd_` classes): the list is rendered server-side, every posting on one
page, each a link to `/jobs/detail/<slug>-<id>` with its office and contract
type. The posting page holds the advert in `x_wd_job_description` and a side
card of Department / Office / Contract type.

What is handled here:
  * A side-card field can be left blank (two postings have no contract
    type), so each field is read within its own <li>.
  * The department filter shows a count beside each department and beside
    "All Departments". That last count is what the run checks itself
    against; the per-department counts are checked to add up to it too, so
    a posting left out of the default view would show as a mismatch.

Output and the completeness check are in extract_common.py.
Usage:  python3 extractAerospacelabPositions.py
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from extract_common import DETAIL_WORKERS, finish_run, html_to_text, inner_html, request_text

NAME = "Aerospacelab"
SITE = "https://www.aerospacelab.com"
LIST_URL = f"{SITE}/jobs?all_departments=1"
OUT_DIR = Path(__file__).resolve().parent / "Aerospacelab"

_CARD_RE = re.compile(r'<a href="(/jobs/detail/[^"?#]+-(\d+))">(.*?)</a>', re.S)
_CARD_TITLE_RE = re.compile(r'class="x_wd_jobs_title[^"]*">(.*?)</h3>', re.S)
_CARD_FIELD_RE = re.compile(r'class="x_wd_jobs_(country|contract)">.*?<span[^>]*>(.*?)</span>', re.S)
_BADGE_RE = re.compile(
    r'<a href="/jobs\?(all_departments=1|department_id=\d+)[^"]*"[^>]*>\s*[^<]*?'
    r'<span class="badge[^"]*">(\d+)</span>'
)
_SIDE_ITEM_RE = re.compile(r"<li[^>]*>\s*<h5[^>]*>\s*([^<]+?)\s*</h5>(.*?)</li>", re.S)
_SIDE_VALUE_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.S)
_POSTED_RE = re.compile(r'itemprop="datePosted" content="(\d{4}-\d{2}-\d{2})')


def _clean(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def list_postings() -> tuple[dict[str, dict[str, Any]], int]:
    """Every posting card on the list, keyed by Odoo job id, and the site's count."""
    page = request_text(LIST_URL)
    found: dict[str, dict[str, Any]] = {}
    for path, job_id, card in _CARD_RE.findall(page):
        title = _CARD_TITLE_RE.search(card)
        fields = {name: _clean(value) for name, value in _CARD_FIELD_RE.findall(card)}
        found.setdefault(
            job_id,
            {"path": path, "title": _clean(title.group(1)) if title else "", **fields},
        )

    counts = dict(_BADGE_RE.findall(page))
    total = int(counts.pop("all_departments=1", 0))
    by_department = sum(int(count) for count in counts.values())
    if total and by_department != total:
        print(f"  ! departments add up to {by_department}, not the {total} the site states", file=sys.stderr)
    return found, total or len(found)


def fetch_detail(path: str) -> dict[str, str]:
    try:
        page = request_text(f"{SITE}{path}")
    except RuntimeError as exc:
        print(f"  ! no detail for {path}: {exc}", file=sys.stderr)
        return {}
    # One <li> per field; a field left blank has no <p>, and must stay blank
    # rather than borrow the next heading on the page.
    detail = {}
    for label, item in _SIDE_ITEM_RE.findall(page):
        value = _SIDE_VALUE_RE.search(item)
        detail[label] = _clean(value.group(1)) if value else ""
    detail["_description"] = next(iter(inner_html(page, "class", "x_wd_job_description", token=True)), "")
    posted = _POSTED_RE.search(page)
    detail["_published"] = posted.group(1) if posted else ""
    return detail


def build_record(job_id: str, entry: dict[str, Any], detail: dict[str, str]) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "title": entry["title"],
        "location": detail.get("Office") or entry.get("country", ""),
        "department": detail.get("Department", ""),
        "contract": detail.get("Contract type") or entry.get("contract", ""),
        "published": detail.get("_published", ""),
        "url": f"{SITE}{entry['path']}",
        "description": html_to_text(detail.get("_description", "")),
    }


def main() -> int:
    argparse.ArgumentParser(description=f"Extract every {NAME} posting on its careers page.").parse_args()

    started = datetime.now()
    print(f"{NAME} — every posting on {LIST_URL}")
    listing, total = list_postings()
    print(f"  {len(listing)} postings listed, site states {total}")

    print(f"  fetching {len(listing)} adverts…")
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        details = list(pool.map(fetch_detail, (entry["path"] for entry in listing.values())))

    return finish_run(
        name=NAME,
        out_dir=OUT_DIR,
        query="",
        source=LIST_URL,
        started=started,
        total=total,
        records=[
            build_record(job_id, entry, detail)
            for (job_id, entry), detail in zip(listing.items(), details)
        ],
        missing_detail=sum(1 for detail in details if not detail.get("_description")),
    )


if __name__ == "__main__":
    sys.exit(main())

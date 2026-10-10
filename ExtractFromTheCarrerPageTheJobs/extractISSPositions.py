#!/usr/bin/env python3
"""Every current opportunity of the EU Institute for Security Studies (EUISS).

    https://www.iss.europa.eu/opportunities

A Drupal page with two tabs, "Current" and "Archived", which are two blocks
of the same HTML (#tab-current, #tab-archived) rather than two addresses;
the unfiltered list on the page mixes both. Only the Current block is taken.
Each opportunity page holds the full notice; the list's teaser has the
reference, status, place, starting date and deadline when the notice
gives them.

What is handled here:
  * The page states no count; the run checks itself against the links in
    the Current block.
  * A Current block long enough to be paged would carry its own pager; its
    pages are followed if so.

Output and the completeness check are in extract_common.py.
Usage:  python3 extractISSPositions.py
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

NAME = "ISS"
SITE = "https://www.iss.europa.eu"
LIST_URL = f"{SITE}/opportunities"
OUT_DIR = Path(__file__).resolve().parent / "ISS"
MAX_PAGES = 20

_TEASER_LINK_RE = re.compile(r'class="teaser-title">\s*<a href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_FIELD_RE = re.compile(r"<strong>\s*([^<:]+?)\s*:?\s*</strong>\s*:?\s*([^<]*)")
_DEADLINE_RE = re.compile(r'class="deadline-entry">.*?</span>(.*?)</div>', re.S)
_PAGER_RE = re.compile(r'href="\?page=(\d+)"')
_TEASER_FIELDS = (
    ("Status", "contract"),
    ("Place of employment", "location"),
    ("Starting date", "starting_date"),
)


def _clean(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip().strip("\xa0")


def _teasers(block: str) -> dict[str, dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for teaser in inner_html(block, "class", "teaser-body", token=True):
        link = _TEASER_LINK_RE.search(teaser)
        if not link:
            continue
        fields: dict[str, str] = {"title": _clean(link.group(2))}
        for label, value in _FIELD_RE.findall(teaser):
            label = _clean(label)
            if label.lower().startswith("ref"):
                # "Ref: EUISS(2026)/TEMP/03" sits inside the strong tag.
                fields["reference"] = re.sub(r"^Ref\.?\s*:?\s*", "", label, flags=re.I) or _clean(value)
            else:
                fields[label] = _clean(value)
        deadline = _DEADLINE_RE.search(teaser)
        fields["deadline_text"] = _clean(deadline.group(1)) if deadline else ""
        found.setdefault(html.unescape(link.group(1)), fields)
    return found


def list_current() -> tuple[dict[str, dict[str, str]], int]:
    found: dict[str, dict[str, str]] = {}
    linked: set[str] = set()
    pages = [0]
    seen_pages: set[int] = set()
    while pages and len(seen_pages) < MAX_PAGES:
        number = pages.pop(0)
        if number in seen_pages:
            continue
        seen_pages.add(number)
        page = request_text(LIST_URL if number == 0 else f"{LIST_URL}?page={number}")
        block = next(iter(inner_html(page, "id", "tab-current")), "")
        found.update({path: fields for path, fields in _teasers(block).items() if path not in found})
        linked.update(html.unescape(path) for path, _ in _TEASER_LINK_RE.findall(block))
        pages.extend(int(n) for n in _PAGER_RE.findall(block) if int(n) not in seen_pages)
    return found, len(linked | found.keys())


def _deadline(text: str) -> str:
    match = re.match(r"(\d{1,2} \w+ \d{4})", text)
    if not match:
        return ""
    try:
        return datetime.strptime(match.group(1), "%d %B %Y").date().isoformat()
    except ValueError:
        return ""


def fetch_notice(path: str) -> str:
    try:
        page = request_text(f"{SITE}{path}")
    except RuntimeError as exc:
        print(f"  ! no detail for {path}: {exc}", file=sys.stderr)
        return ""
    # The notice page opens with its deadline (a column already) and then the
    # notice itself, in its own content block.
    node = next(iter(inner_html(page, "class", "node--view-mode-full", token=True)), "")
    content = next(iter(inner_html(node, "class", "content-main", token=True)), node)
    return html_to_text(content)


def build_record(path: str, teaser: dict[str, str], notice: str) -> dict[str, Any]:
    record: dict[str, Any] = {
        "job_id": teaser.get("reference") or path.rsplit("/", 1)[-1],
        "title": teaser["title"],
    }
    for label, column in _TEASER_FIELDS:
        record[column] = teaser.get(label, "")
    record.update(
        {
            "deadline": _deadline(teaser.get("deadline_text", "")),
            "deadline_text": teaser.get("deadline_text", ""),
            "url": f"{SITE}{path}",
            "description": notice,
        }
    )
    return record


def main() -> int:
    argparse.ArgumentParser(description=f"Extract every current {NAME} opportunity.").parse_args()

    started = datetime.now()
    print(f"{NAME} — current opportunities on {LIST_URL}")
    teasers, total = list_current()
    print(f"  {len(teasers)} current opportunities")

    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        notices = list(pool.map(fetch_notice, teasers))

    return finish_run(
        name=NAME,
        out_dir=OUT_DIR,
        query="current",
        source=f"{LIST_URL}#tab-current",
        started=started,
        total=total,
        records=[build_record(path, teaser, notice) for (path, teaser), notice in zip(teasers.items(), notices)],
        missing_detail=sum(1 for notice in notices if not notice),
    )


if __name__ == "__main__":
    sys.exit(main())

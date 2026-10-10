#!/usr/bin/env python3
"""Every EUROCONTROL staff vacancy on its job portal.

    https://jobs.eurocontrol.int/eurocontrol-vacancies/

A WordPress site whose list is filled by the Lumesse TalentLink plugin: the
page has no postings in its HTML, and its script asks

    POST https://jobs.eurocontrol.int/wp-admin/admin-ajax.php
         action=lumesse_ajax_grid_list, params[i][key]/params[i][val] ...

for an HTML fragment of postings plus `advertsCount`, the number shown above
the list. Each posting page has a "Label: value" list (reference, location,
grade, number of posts, duration, directorate, publication and closing
dates, …) followed by the advert.

What is handled here:
  * The page narrows the search with a hidden "EUROCONTROL Staff" field
    (customField4); it is sent too, or the count would differ from the page.
  * `per_page` is raised so one request holds the whole list, and the run
    checks itself against `advertsCount`.

Output and the completeness check are in extract_common.py.
Usage:  python3 extractEurocontrolPositions.py
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from extract_common import DETAIL_WORKERS, finish_run, html_to_text, inner_html, request_form, request_text

NAME = "Eurocontrol"
SITE = "https://jobs.eurocontrol.int"
LIST_URL = f"{SITE}/eurocontrol-vacancies/?date=all&keywords=&sort=alphabetical"
AJAX_URL = f"{SITE}/wp-admin/admin-ajax.php"
OUT_DIR = Path(__file__).resolve().parent / "Eurocontrol"
PER_PAGE = 200
MAX_PAGES = 20

_HIDDEN_RE = re.compile(r'<input[^>]*type="hidden"[^>]*class="[^"]*checkbox[^"]*"[^>]*>', re.S)
_ATTR_RE = r'{}="([^"]*)"'
_JOB_LINK_RE = re.compile(r'href="(https://jobs\.eurocontrol\.int/jobs/[^"?#]+)"')
_FIELD_RE = re.compile(r"<li>\s*<strong>\s*([^<:]+?)\s*:\s*</strong>(.*?)</li>", re.S)
_LINK_TEXT_RE = re.compile(r"<a\b.*?</a>", re.S)
#: The side panel's dates, in UTC: writePostingDate("…_posting_start_date", "2026-09-09 22:00:00", …).
_POSTING_DATE_RE = re.compile(r'writePostingDate\("postingDate_posting_(start|end)_date",\s*"([^"]+)"')
TIME_ZONE = ZoneInfo("Europe/Brussels")
_FIELDS = (
    ("Reference", "reference"),
    ("Location", "location"),
    ("Type of post", "category"),
    ("Job level", "grade"),
    ("Number of posts", "posts"),
    ("Duration of appointment", "duration"),
    ("Nature of competition", "competition"),
    ("Directorate/Service", "directorate"),
    ("Security clearance", "security_clearance"),
)


def _clean(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip(" -")


def _hidden_criteria() -> list[tuple[str, str]]:
    """The hidden search fields the list page sends with every search."""
    page = request_text(LIST_URL)
    box = page[page.find('id="advert-searchbox"') :]
    criteria = []
    for tag in _HIDDEN_RE.findall(box):
        name = re.search(_ATTR_RE.format("name"), tag)
        value = re.search(_ATTR_RE.format("value"), tag)
        if name:
            criteria.append((html.unescape(name.group(1)), html.unescape(value.group(1) if value else "")))
    return criteria


def list_postings() -> tuple[list[str], int]:
    criteria = _hidden_criteria()
    found: dict[str, None] = {}
    total = 0
    for page in range(1, MAX_PAGES + 1):
        params = criteria + [
            ("date", "all"),
            ("keywords", ""),
            ("sort", "alphabetical"),
            ("per_page", str(PER_PAGE)),
            ("page_job", str(page)),
        ]
        form = {"action": "lumesse_ajax_grid_list", "lanugage": "1", "sendEvent": "false"}
        for index, (key, value) in enumerate(params):
            form[f"params[{index}][key]"] = key
            form[f"params[{index}][val]"] = value
        payload = json.loads(request_form(AJAX_URL, form))
        total = int(payload.get("advertsCount") or total)
        links = _JOB_LINK_RE.findall(payload.get("advertsHtml") or "")
        found.update(dict.fromkeys(links))
        if not links or len(found) >= total:
            break
    return list(found), total


def fetch_posting(url: str) -> dict[str, str]:
    try:
        page = request_text(url)
    except RuntimeError as exc:
        print(f"  ! no detail for {url}: {exc}", file=sys.stderr)
        return {}
    body = next(iter(inner_html(page, "class", "rich-editor-text")), "")
    # A value can end in a link ("AD6-AD9 - Check salary simulations…"); the
    # link's text is not part of the value.
    detail = {_clean(label): _clean(_LINK_TEXT_RE.sub("", value)) for label, value in _FIELD_RE.findall(body)}
    for which, moment in _POSTING_DATE_RE.findall(page):
        utc = datetime.strptime(moment, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        detail[f"_{which}"] = utc.astimezone(TIME_ZONE).date().isoformat()
    title = re.search(r"<title>(.*?)(?: [–-] EUROCONTROL.*)?</title>", page, re.S)
    detail["_title"] = _clean(title.group(1)) if title else ""
    detail["_advert"] = html_to_text(body)
    return detail


def _date(text: str) -> str:
    match = re.match(r"(\d{2}/\d{2}/\d{4})", text)
    return datetime.strptime(match.group(1), "%d/%m/%Y").date().isoformat() if match else ""


def build_record(url: str, detail: dict[str, str]) -> dict[str, Any]:
    record: dict[str, Any] = {
        "job_id": detail.get("Reference") or url.rstrip("/").rsplit("/", 1)[-1],
        "title": detail.get("Function title") or detail.get("_title", ""),
    }
    for label, column in _FIELDS:
        if column != "reference":
            record[column] = detail.get(label, "")
    record.update(
        {
            # Not every advert states its publication date; the side panel does.
            "published": _date(detail.get("Competition publication date", "")) or detail.get("_start", ""),
            "deadline": _date(detail.get("Competition closing date", "")) or detail.get("_end", ""),
            "url": url,
            "description": detail.get("_advert", ""),
        }
    )
    return record


def main() -> int:
    argparse.ArgumentParser(description=f"Extract every {NAME} staff vacancy.").parse_args()

    started = datetime.now()
    print(f"{NAME} — every staff vacancy on {LIST_URL}")
    urls, total = list_postings()
    print(f"  {len(urls)} postings listed, site states {total}")

    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        details = list(pool.map(fetch_posting, urls))

    return finish_run(
        name=NAME,
        out_dir=OUT_DIR,
        query="",
        source=LIST_URL,
        started=started,
        total=total,
        records=[build_record(url, detail) for url, detail in zip(urls, details)],
        missing_detail=sum(1 for detail in details if not detail.get("_advert")),
    )


if __name__ == "__main__":
    sys.exit(main())

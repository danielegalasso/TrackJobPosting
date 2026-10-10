"""Extract every posting from an Oracle Taleo career section (the classic .ftl one).

A career section such as
    https://nato.taleo.net/careersection/2/jobsearch.ftl?lang=en
renders its first page of results into a hidden `initialHistory` field: a
long string of values separated by `!|!`, with "Search Results (54 jobs
found)" and `listRequisition.nbElements` among them. The pager's arrows post
the page's own form `ftlform` back to `jobsearch.ajax` with
`rlPager.currentPage=N`, and get the same kind of string for that page.
A posting's own page, `jobdetail.ftl?job=<job number>`, carries its fields
and the advert the same way.

Taleo traps handled here:
  * The first page embedded in the HTML is sorted differently from the pages
    the pager returns. Joining it to pages 2 and 3, as a scraper following
    the arrows would, gives NATO's 54 postings as 38 distinct ones: 16 come
    twice and 16 never. Every page, the first included, is therefore asked
    of the pager, and the walk is repeated until the distinct postings match
    `nbElements`.
  * Values are percent-encoded and then backslash-escaped ("11%5C:59" is
    "11:59"); both layers are undone.
  * A posting's fields are not labelled in the string, and some postings
    carry extra blank fields, so positions are counted from the closing date,
    the one value that is easy to recognise; salary and currency are checked
    to look like a number and a "(EUR)" code before they are kept.

Output and the completeness check are in extract_common.py. Each
organisation has a small entry script that names its section and calls
`main`.
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

from extract_common import DETAIL_WORKERS, finish_run, html_to_text, request_form, request_text

#: Full walks of the list before giving up on reaching `nbElements`.
MAX_WALKS = 4
MAX_PAGES = 200

_HISTORY_RE = re.compile(r'id="initialHistory"[^>]*value="([^"]*)"')
_FORM_RE = re.compile(r'<form name="ftlform".*?</form>', re.S)
_HIDDEN_RE = re.compile(r'<input type="hidden" name="([^"]+)" id="[^"]*" value="([^"]*)"')
_COUNT_RE = re.compile(r'id="listRequisition\.nbElements" value="(\d+)"')
_CLOSING_RE = re.compile(r"\d{1,2}-[A-Z][a-z]{2}-\d{4}")
_SALARY_RE = re.compile(r"^\d[\d,.]*$")
_CURRENCY_RE = re.compile(r"\([A-Z]{3}\)")


@dataclass(frozen=True)
class Section:
    """One classic Taleo career section."""

    name: str
    host: str
    section: str
    lang: str
    out_dir: Path

    @property
    def base(self) -> str:
        return f"https://{self.host}/careersection/{self.section}"

    @property
    def search_url(self) -> str:
        return f"{self.base}/jobsearch.ftl?lang={self.lang}"

    def detail_url(self, job_number: str) -> str:
        return f"{self.base}/jobdetail.ftl?job={job_number}&lang={self.lang}"


def _decode(value: str) -> str:
    """Undo Taleo's two layers: percent-encoding, then backslash escapes."""
    return re.sub(r"\\(.)", r"\1", urllib.parse.unquote(value))


def _values(payload: str) -> list[str]:
    return html.unescape(payload).split("!|!")


def _listing_entries(values: list[str]) -> list[dict[str, str]]:
    """Postings in one page's values.

    Each starts with the internal id, the title, the id and title again, the
    id five more times, then the job number and location; the closing date,
    organisation and grade follow a few flags later.
    """
    found = []
    for i in range(len(values) - 20):
        contest = values[i]
        if not (
            contest.isdigit()
            and values[i + 2] == contest
            and values[i + 1] == values[i + 3]
            and all(values[i + k] == contest for k in range(4, 9))
            and values[i + 9].isdigit()
        ):
            continue
        tail = values[i + 11 : i + 22]
        closing = next((k for k, value in enumerate(tail) if _CLOSING_RE.match(_decode(value))), None)
        found.append(
            {
                "contest": contest,
                "title": _decode(values[i + 1]).strip(),
                "job_number": values[i + 9],
                "location": _decode(values[i + 10]),
                "closing": _decode(tail[closing]) if closing is not None else "",
                "organisation": _decode(tail[closing + 1]) if closing is not None else "",
                "grade": _decode(tail[closing + 2]) if closing is not None else "",
            }
        )
    return found


def list_postings(section: Section) -> tuple[dict[str, dict[str, str]], int]:
    """Every posting, keyed by job number, and the count the section states."""
    found: dict[str, dict[str, str]] = {}
    total = 0
    for walk in range(1, MAX_WALKS + 1):
        page = request_text(section.search_url)
        count = _COUNT_RE.search(page)
        total = int(count.group(1)) if count else total
        form = _FORM_RE.search(page)
        fields = {
            html.unescape(name): html.unescape(value)
            for name, value in reversed(_HIDDEN_RE.findall(form.group(0) if form else ""))
        }
        for number in range(1, MAX_PAGES + 1):
            payload = request_form(
                f"{section.base}/jobsearch.ajax",
                {
                    **fields,
                    "ftlpageid": "reqListBasicPage",
                    "ftlinterfaceid": "requisitionListInterface",
                    "ftlcompid": "rlPager",
                    "ftlcompclass": "PagerComponent",
                    "ftlcallback": "requisition_restoreDatesValues",
                    "ftlajaxid": "ftlx1",
                    "rlPager.currentPage": str(number),
                    "lang": section.lang,
                },
            )
            entries = _listing_entries(_values(payload))
            for entry in entries:
                found.setdefault(entry["job_number"], entry)
            if not entries or len(found) >= total:
                break
        print(f"  walk {walk}: {len(found)} distinct postings of {total} stated")
        if len(found) >= total:
            break
    return found, total


def fetch_detail(section: Section, job_number: str) -> dict[str, str]:
    """A posting page's fields, found by position after its job number."""
    try:
        page = request_text(section.detail_url(job_number))
    except RuntimeError as exc:
        print(f"  ! no detail for {job_number}: {exc}", file=sys.stderr)
        return {}
    history = _HISTORY_RE.search(page)
    values = _values(history.group(1)) if history else []
    detail: dict[str, str] = {}
    # The advert is the long value marked "!*!" at its start.
    adverts = [value for value in values if value.startswith("!*!")]
    if adverts:
        detail["advert"] = _decode(max(adverts, key=len)[3:])
    # Each field comes twice (value, then value again for display), and some
    # postings carry extra blank fields after the location, so positions are
    # counted from the closing date, the one value that is easy to recognise.
    at = next((i for i, value in enumerate(values) if value == job_number), None)
    closing = next(
        (i for i in range(at + 1, min(at + 20, len(values))) if _CLOSING_RE.match(_decode(values[i]))),
        None,
    ) if at is not None else None
    if closing is not None:
        detail["schedule"] = _decode(values[closing - 2])
        after = [_decode(value) for value in values[closing + 2 : closing + 6]]
        if len(after) == 4 and _SALARY_RE.match(after[0]) and _CURRENCY_RE.search(after[2]):
            detail.update({"salary": after[0], "currency": after[2], "frequency": after[3]})
    return detail


def _closing_date(text: str) -> str:
    match = _CLOSING_RE.match(text)
    return datetime.strptime(match.group(0), "%d-%b-%Y").date().isoformat() if match else ""


def build_record(section: Section, entry: dict[str, str], detail: dict[str, str]) -> dict[str, Any]:
    salary = " ".join(
        part for part in (detail.get("salary"), detail.get("currency"), detail.get("frequency")) if part
    )
    return {
        "job_id": entry["job_number"],
        "title": entry["title"],
        "location": entry["location"],
        "organisation": entry["organisation"],
        "grade": entry["grade"],
        "schedule": detail.get("schedule", ""),
        "salary": salary,
        "deadline": _closing_date(entry["closing"]),
        "deadline_text": entry["closing"],
        "url": section.detail_url(entry["job_number"]),
        "description": html_to_text(detail.get("advert", "")),
    }


def main(section: Section) -> int:
    argparse.ArgumentParser(description=f"Extract every {section.name} posting on its Taleo section.").parse_args()

    started = datetime.now()
    print(f"{section.name} Taleo — every posting on {section.search_url}")
    listing, total = list_postings(section)

    print(f"  fetching {len(listing)} adverts…")
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        details = list(pool.map(lambda number: fetch_detail(section, number), listing))

    return finish_run(
        name=section.name,
        out_dir=section.out_dir,
        query="",
        source=section.search_url,
        started=started,
        total=total,
        records=[build_record(section, entry, detail) for entry, detail in zip(listing.values(), details)],
        missing_detail=sum(1 for detail in details if not detail.get("advert")),
    )

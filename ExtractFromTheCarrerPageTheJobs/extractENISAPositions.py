#!/usr/bin/env python3
"""ENISA vacancies, by status, from its careers page.

    https://www.enisa.europa.eu/careers

A Drupal view, ten vacancies a page (`&page=N`), filterable by status. The
status filter's values do not read as their labels:

    careers-published  "Open"       applications being accepted
    careers-closed     "Ongoing"    applications closed, selection under way
    careers-cancelled  "Cancelled"
    careers-completed  "Completed"

so a link with `moderation_state=careers-closed` shows the selections in
progress, not an archive. The unfiltered view is the whole archive back to
2010 (140 vacancies when this was written). By default the vacancies still
in play are taken, Open and Ongoing, each with its status.

Each vacancy page has a short summary with "Label: value" lines (contract,
grade, area, place, probation, reserve list), the reference and closing date,
an "Apply here" link to the e-recruitment platform, and the full vacancy
notice as a PDF, whose text is taken with pdftotext when it is installed.

What is handled here:
  * The view states no count, so the run checks itself against the
    unfiltered view: the vacancies there with the chosen statuses must be
    exactly the ones the filtered views returned.

Output and the completeness check are in extract_common.py.
Usage:  python3 extractENISAPositions.py [--status open,ongoing | all | ...]
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from extract_common import DETAIL_WORKERS, finish_run, html_to_text, inner_html, pdf_text, request_text

NAME = "ENISA"
SITE = "https://www.enisa.europa.eu"
LIST_URL = f"{SITE}/careers"
OUT_DIR = Path(__file__).resolve().parent / "ENISA"
#: Status as the page labels it -> the filter's value for it.
STATUSES = {
    "open": "careers-published",
    "ongoing": "careers-closed",
    "cancelled": "careers-cancelled",
    "completed": "careers-completed",
}
DEFAULT_STATUSES = "open,ongoing"
MAX_PAGES = 100

_ROW_RE = re.compile(r'<tr class="career-item">(.*?)</tr>', re.S)
_CELL_RE = re.compile(r"data-label=([A-Z ]+?)>(.*?)</td>", re.S)
_LINK_RE = re.compile(r'href="([^"]+)"')
_BODY_FIELD_RE = re.compile(r"<strong>\s*([^<:]+?)\s*:?\s*(?:&nbsp;)?\s*</strong>\s*:?\s*([^<]*)")
_META_RE = re.compile(r'<span class="label-detail">\s*([^<:]+?)\s*:?\s*</span>\s*(?:<span class="text">)?(.*?)</(?:span|li)>', re.S)
_APPLY_RE = re.compile(r'<a href="(https://opportunities\.enisa\.europa\.eu/[^"]+)"[^>]*>\s*Apply here')
_NOTICE_RE = re.compile(r'<li class="file pdf">.*?href="([^"]+\.pdf)"', re.S)
_SUMMARY_FIELDS = (
    ("Function Group and grade", "grade"),
    ("Area", "area"),
    ("Place of Employment", "location"),
    ("Probation period", "probation"),
    ("Reserve list", "reserve_list"),
)


def _clean(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _list_url(status: str, page: int) -> str:
    query = {
        "combine": "",
        "field_vacancy_type_target_id": "All",
        "moderation_state": status,
        "field_expires_year": "All",
        "page": page,
    }
    return f"{LIST_URL}?{urllib.parse.urlencode(query)}"


def list_rows(status: str) -> dict[str, dict[str, str]]:
    """Every row of the view for one status filter value, keyed by vacancy path."""
    found: dict[str, dict[str, str]] = {}
    for page_number in range(MAX_PAGES):
        rows = _ROW_RE.findall(request_text(_list_url(status, page_number)))
        for row in rows:
            link = _LINK_RE.search(row)
            if not link:
                continue
            cells = {label.strip(): _clean(value) for label, value in _CELL_RE.findall(row)}
            found.setdefault(html.unescape(link.group(1)), cells)
        if not rows:
            break
    return found


def _closing(text: str) -> str:
    try:
        return datetime.strptime(text, "%d %B, %Y").date().isoformat()
    except ValueError:
        return ""


def fetch_vacancy(path: str) -> dict[str, Any]:
    try:
        page = request_text(f"{SITE}{path}")
    except RuntimeError as exc:
        print(f"  ! no detail for {path}: {exc}", file=sys.stderr)
        return {}
    detail: dict[str, Any] = {}
    summary = ""
    # Other blocks on the page ("Learn more about the topics") use the same
    # field class, so the summary is looked for inside the content column.
    content = next(iter(inner_html(page, "class", "publication-content", token=True)), "")
    summary = next(iter(inner_html(content, "class", "field--name-body", token=True)), "")
    for label, value in _BODY_FIELD_RE.findall(summary):
        detail[_clean(label)] = _clean(value)
    for label, value in _META_RE.findall(page):
        detail.setdefault(_clean(label), _clean(value))
    apply_link = _APPLY_RE.search(page)
    notice = _NOTICE_RE.search(page)
    detail["_apply"] = html.unescape(apply_link.group(1)) if apply_link else ""
    detail["_notice"] = urllib.parse.urljoin(SITE, html.unescape(notice.group(1))) if notice else ""
    detail["_summary"] = html_to_text(summary)
    detail["_notice_text"] = pdf_text(detail["_notice"]) if detail["_notice"] else ""
    return detail


def build_record(path: str, row: dict[str, str], detail: dict[str, Any]) -> dict[str, Any]:
    record: dict[str, Any] = {
        "job_id": row.get("REFERENCE") or detail.get("Ref. Code") or path.rsplit("/", 1)[-1],
        "title": row.get("TITLE", ""),
        "status": row.get("STATUS") or detail.get("Status", ""),
        "contract": row.get("TYPE") or detail.get("Vacancy type", ""),
    }
    for label, column in _SUMMARY_FIELDS:
        record[column] = detail.get(label, "")
    record.update(
        {
            "deadline": _closing(row.get("CLOSING DATE", "")),
            "url": f"{SITE}{path}",
            "apply_url": detail.get("_apply", ""),
            "notice_pdf": detail.get("_notice", ""),
            # The page's summary, then the notice itself, which is the advert.
            "description": "\n\n".join(
                part for part in (detail.get("_summary", ""), detail.get("_notice_text", "")) if part
            ),
        }
    )
    return record


def _chosen(argument: str) -> list[str]:
    if argument.strip() == "all":
        return list(STATUSES)
    chosen = [name.strip() for name in argument.split(",") if name.strip()]
    unknown = [name for name in chosen if name not in STATUSES]
    if unknown:
        raise SystemExit(f"unknown status {', '.join(unknown)}; choose from {', '.join(STATUSES)} or all")
    return chosen


def main() -> int:
    parser = argparse.ArgumentParser(description=f"Extract {NAME} vacancies by status.")
    parser.add_argument(
        "--status",
        default=DEFAULT_STATUSES,
        help=f"comma-separated: {', '.join(STATUSES)}, or all (default {DEFAULT_STATUSES})",
    )
    statuses = _chosen(parser.parse_args().status)

    started = datetime.now()
    print(f"{NAME} — {', '.join(statuses)} vacancies on {LIST_URL}")
    listing: dict[str, dict[str, str]] = {}
    for status in statuses:
        rows = list_rows(STATUSES[status])
        print(f"  {status}: {len(rows)}")
        listing.update(rows)

    # The unfiltered view is the independent count: the same statuses there.
    everything = list_rows("All")
    expected = {path for path, row in everything.items() if row.get("STATUS", "").lower() in statuses}
    print(f"  the unfiltered view has {len(everything)} vacancies, {len(expected)} of them {'/'.join(statuses)}")
    for label, paths in (("filtered", listing.keys() - expected), ("unfiltered", expected - listing.keys())):
        if paths:
            print(f"  ! only in the {label} view: {', '.join(sorted(paths))}", file=sys.stderr)

    print(f"  fetching {len(listing)} vacancies…")
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        details = list(pool.map(fetch_vacancy, listing))

    return finish_run(
        name=NAME,
        out_dir=OUT_DIR,
        query=",".join(statuses),
        source=LIST_URL,
        started=started,
        total=len(listing.keys() | expected),
        records=[build_record(path, row, detail) for (path, row), detail in zip(listing.items(), details)],
        missing_detail=sum(1 for detail in details if not detail.get("_notice_text")),
    )


if __name__ == "__main__":
    sys.exit(main())

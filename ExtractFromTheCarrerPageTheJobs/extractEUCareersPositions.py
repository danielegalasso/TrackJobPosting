#!/usr/bin/env python3
"""Every vacancy on EU Careers, the EPSO job board of the EU institutions.

    https://eu-careers.europa.eu/jobs_en

A Drupal site rendered server-side: the list shows 20 vacancies a page
(`?page=N`) under a "Jobs (94)" heading, and each vacancy page has a details
block of "Label: value" lines (reference, deadline, institution, domain,
contract type, grade, …).

Where the advert itself lives depends on who posted it, and only two of the
three places are this site:
  * on the vacancy page, as body text (most agencies);
  * in a PDF vacancy notice linked from the page (the Commission); the text
    is taken with `pdftotext` from poppler-utils when it is installed, and
    the PDF's URL is recorded either way;
  * on the agency's own recruitment site, behind a "Link to Vacancy" or
    "Vacancy link" (EASA, EDA, …). Those are dozens of different sites, so
    they are not followed; `vacancy_url` points at each.

What is handled here:
  * The list is sorted by deadline and many vacancies share one, so pages
    could reshuffle between requests; the walk is repeated until the number
    of distinct vacancies matches the heading's count.
  * The language switch (`?prefLang=it`) only changes the site's chrome; the
    vacancies are published in English, so the English URLs are used.

Output and the completeness check are in extract_common.py.
Usage:  python3 extractEUCareersPositions.py
"""

from __future__ import annotations

import argparse
import html
import re
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from extract_common import DETAIL_WORKERS, finish_run, html_to_text, inner_html, pdf_text, request_text

NAME = "EUCareers"
SITE = "https://eu-careers.europa.eu"
LIST_URL = f"{SITE}/jobs_en"
OUT_DIR = Path(__file__).resolve().parent / "EUCareers"
#: Full walks of the list before giving up on reaching the heading's count.
MAX_WALKS = 4
MAX_PAGES = 100

_TOTAL_RE = re.compile(r"Jobs\s*\((\d+)\)")
_RESULT_RE = re.compile(r'href="(/jobs/[^"?#]+)[^"]*"[^>]*class="[^"]*eujmp-vacancy-result__title')
_DETAIL_RE = re.compile(r"<p[^>]*>\s*<strong>([^<:]+):</strong>(.*?)</p>", re.S)
_TIME_RE = re.compile(r'<time datetime="([^"]+)"')
_EXTERNAL_RE = re.compile(r'<a href="(https?://[^"]+)"[^>]*>\s*(?:Link to Vacancy|Vacancy link)', re.I)
_DOCUMENT_RE = re.compile(r'href="(/document/download/[^"]+)"')
_APPLICATION_FORM_RE = re.compile(r"application(?:%20|[ _-])*form", re.I)
#: Details labels, mapped to output columns.
_FIELDS = (
    ("Reference", "reference"),
    ("Institution", "institution"),
    ("Location", "location"),
    ("Domain", "domain"),
    ("Contract type", "contract"),
    ("Grade", "grade"),
    ("Directorate-General or Department", "department"),
)


def _total(page: str) -> int:
    match = _TOTAL_RE.search(html.unescape(re.sub(r"<[^>]+>", " ", page)))
    return int(match.group(1)) if match else 0


def list_vacancies() -> tuple[list[str], int]:
    """Every vacancy path on the list, and the count its heading gives."""
    found: dict[str, None] = {}
    total = 0
    for walk in range(1, MAX_WALKS + 1):
        for page_number in range(MAX_PAGES):
            page = request_text(f"{LIST_URL}?page={page_number}")
            total = _total(page) or total
            paths = _RESULT_RE.findall(page)
            found.update(dict.fromkeys(paths))
            if not paths:
                break
        print(f"  walk {walk}: {len(found)} distinct vacancies of {total} reported")
        if len(found) >= total:
            break
    return list(found), total


def _article(page: str) -> str:
    start = page.find("eujmp-vacancy--full")
    if start < 0:
        return ""
    end = page.find("</article>", start)
    return page[start : end if end > 0 else len(page)]


def fetch_vacancy(path: str) -> dict[str, Any]:
    url = f"{SITE}{path}"
    try:
        page = request_text(url)
    except RuntimeError as exc:
        print(f"  ! no detail for {url}: {exc}", file=sys.stderr)
        page = ""
    article = _article(page)

    details = {
        label.strip(): html.unescape(re.sub(r"<[^>]+>", "", value)).strip()
        for label, value in _DETAIL_RE.findall(article)
    }
    deadline = _TIME_RE.search(article)
    external = _EXTERNAL_RE.search(article)
    documents = [f"{SITE}{html.unescape(link)}" for link in _DOCUMENT_RE.findall(article)]
    notices = [link for link in documents if not _APPLICATION_FORM_RE.search(link)]

    body = html_to_text("\n".join(inner_html(article, "class", "ecl")))
    source = "page" if body else ""
    if not body and notices:
        body = pdf_text(notices[0])
        source = "pdf" if body else ""
    if not body and external:
        source = "agency site"

    record: dict[str, Any] = {
        "job_id": details.get("Reference", path.rsplit("/", 1)[-1]),
        # The details block does not repeat the title; the page's <title> has it.
        "title": _title(page) or path,
    }
    for label, column in _FIELDS:
        record[column] = details.get(label, "")
    record.update(
        {
            "deadline": deadline.group(1)[:10] if deadline else "",
            "url": url,
            "vacancy_url": html.unescape(external.group(1)) if external else "",
            "notice_pdf": notices[0] if notices else "",
            "advert_source": source,
            "description": body,
        }
    )
    return record


def _title(page: str) -> str:
    match = re.search(r"<title>(.*?)(?: - EU Careers.*)?</title>", page, re.S)
    return html.unescape(match.group(1)).strip() if match else ""


def main() -> int:
    argparse.ArgumentParser(description="Extract every vacancy on EU Careers.").parse_args()

    started = datetime.now()
    print(f"{NAME} — every vacancy on {LIST_URL}")
    paths, total = list_vacancies()
    if not shutil.which("pdftotext"):
        print("  note: pdftotext not installed; Commission notices keep only their PDF link")

    print(f"  fetching {len(paths)} vacancies…")
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        records = list(pool.map(fetch_vacancy, paths))
    # The site rate-limits (HTTP 429) a parallel walk; a vacancy whose page
    # was refused has nothing but its path, and is asked for again, alone.
    refused = [index for index, path in enumerate(paths) if records[index]["title"] == path]
    if refused:
        print(f"  asking again for {len(refused)} vacancies the site refused…")
        for index in refused:
            records[index] = fetch_vacancy(paths[index])

    by_source: dict[str, int] = {}
    for record in records:
        by_source[record["advert_source"] or "none"] = by_source.get(record["advert_source"] or "none", 0) + 1
    print("  advert taken from: " + ", ".join(f"{key} {value}" for key, value in sorted(by_source.items())))

    return finish_run(
        name=NAME,
        out_dir=OUT_DIR,
        query="",
        source=LIST_URL,
        started=started,
        total=total,
        records=records,
        # Adverts kept on an agency's own site are not a failure of this run.
        missing_detail=sum(1 for record in records if not record["advert_source"]),
    )


if __name__ == "__main__":
    sys.exit(main())

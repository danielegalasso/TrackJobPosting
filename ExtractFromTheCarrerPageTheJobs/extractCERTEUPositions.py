#!/usr/bin/env python3
"""Every CERT-EU vacancy on its own website.

    https://cert.europa.eu/vacancies

A small server-rendered page: each vacancy is one `open_vc--opportunity`
card with its deadline, contract type, title and team, linking to
`/vacancies/<slug>`, whose `<article class="font-amender-apply">` holds the
advert.

What is handled here:
  * The page states no total, so the cards are checked against the distinct
    /vacancies/<slug> links on the same page; a card the parser missed would
    leave a link unaccounted for.
  * The deadline is printed in words ("Saturday, October 10, 2026 01:59:00 AM
    CEST") or as "No deadline" for rolling recruitment; it is stored as an
    ISO date, blank when there is none.
  * The advert starts with the team's picture and caption, which is dropped.

Output and the completeness check are in extract_common.py.
Usage:  python3 extractCERTEUPositions.py
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

NAME = "CERTEU"
SITE = "https://cert.europa.eu"
LIST_URL = f"{SITE}/vacancies"
OUT_DIR = Path(__file__).resolve().parent / "CERTEU"

_LINK_RE = re.compile(r'href="(/vacancies/[^"?#]+)"')
_CARD_PARTS = {
    "deadline": re.compile(r'open_vc--opportunity--deadline">.*?<time[^>]*>(.*?)</time>', re.S),
    "contract": re.compile(r'open_vc--opportunity--type">.*?<span>(.*?)</span>', re.S),
    "title": re.compile(r'open_vc--opportunity--title">(.*?)</h3>', re.S),
    "team": re.compile(r'open_vc--opportunity--team">.*?<span>(.*?)</span>', re.S),
}
_FIGURE_RE = re.compile(r"<figure\b.*?</figure>", re.S)


def _clean(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _deadline(text: str) -> str:
    match = re.match(r"\w+, (\w+ \d{1,2}, \d{4})", text)
    if not match:
        return ""
    return datetime.strptime(match.group(1), "%B %d, %Y").date().isoformat()


def list_vacancies() -> tuple[dict[str, dict[str, str]], int]:
    """Every vacancy card, keyed by its path, and the number of vacancy links."""
    page = request_text(LIST_URL)
    found: dict[str, dict[str, str]] = {}
    for card in inner_html(page, "class", "open_vc--opportunity", token=True):
        link = _LINK_RE.search(card)
        if not link:
            continue
        fields = {}
        for name, pattern in _CARD_PARTS.items():
            match = pattern.search(card)
            fields[name] = _clean(match.group(1)) if match else ""
        found.setdefault(link.group(1), fields)
    linked = set(_LINK_RE.findall(page))
    missed = linked - found.keys()
    if missed:
        print(f"  ! linked but not read as a card: {', '.join(sorted(missed))}", file=sys.stderr)
    return found, len(linked | found.keys())


def fetch_advert(path: str) -> str:
    try:
        page = request_text(f"{SITE}{path}")
    except RuntimeError as exc:
        print(f"  ! no detail for {path}: {exc}", file=sys.stderr)
        return ""
    article = next(iter(inner_html(page, "class", "font-amender-apply", token=True)), "")
    return html_to_text(_FIGURE_RE.sub("", article))


def build_record(path: str, card: dict[str, str], advert: str) -> dict[str, Any]:
    return {
        "job_id": path.rsplit("/", 1)[-1],
        "title": card["title"],
        # Every CERT-EU post is in Brussels; the page says so for all of them.
        "location": "Brussels, Belgium",
        "team": card["team"],
        "contract": card["contract"],
        "deadline": _deadline(card["deadline"]),
        "deadline_text": card["deadline"],
        "url": f"{SITE}{path}",
        "description": advert,
    }


def main() -> int:
    argparse.ArgumentParser(description=f"Extract every {NAME} vacancy.").parse_args()

    started = datetime.now()
    print(f"{NAME} — every vacancy on {LIST_URL}")
    cards, total = list_vacancies()
    print(f"  {len(cards)} vacancy cards, {total} vacancies linked")

    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        adverts = list(pool.map(fetch_advert, cards))

    return finish_run(
        name=NAME,
        out_dir=OUT_DIR,
        query="",
        source=LIST_URL,
        started=started,
        total=total,
        records=[build_record(path, card, advert) for (path, card), advert in zip(cards.items(), adverts)],
        missing_detail=sum(1 for advert in adverts if not advert),
    )


if __name__ == "__main__":
    sys.exit(main())

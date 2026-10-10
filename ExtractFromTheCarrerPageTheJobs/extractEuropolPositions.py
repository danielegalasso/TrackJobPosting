#!/usr/bin/env python3
"""Every Europol open vacancy.

    https://www.europol.europa.eu/work-with-us/careers/open-vacancies

The page is a React app, but the server renders its data into the page as
`window.SERVER_DATA`: the vacancy list (with `totalItems`) on the list page,
and the full vacancy (reference, contract, unit, dates, advert, application
link) on each /vacancy/<id> page. Europol recruits through Lumesse
TalentLink; the application link points there.

What is handled here:
  * Dates are Unix timestamps; they are turned into dates in Europol's own
    time zone (The Hague), so a 23:59 deadline is not shown as the next day.
  * "Restricted" posts are open only to candidates from the competent law
    enforcement authorities; `post_type` keeps Europol's wording.
  * The list is paged (`?page=N`) when it outgrows one page; pages are
    followed until `totalItems` is reached.

Output and the completeness check are in extract_common.py.
Usage:  python3 extractEuropolPositions.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from extract_common import DETAIL_WORKERS, finish_run, html_to_text, request_text

NAME = "Europol"
SITE = "https://www.europol.europa.eu"
LIST_URL = f"{SITE}/work-with-us/careers/open-vacancies"
OUT_DIR = Path(__file__).resolve().parent / "Europol"
TIME_ZONE = ZoneInfo("Europe/Amsterdam")
MAX_PAGES = 50

_SERVER_DATA_RE = re.compile(r"window\.SERVER_DATA=(\{.*?\});?\s*</script>", re.S)


def _node(url: str) -> dict[str, Any]:
    match = _SERVER_DATA_RE.search(request_text(url))
    if not match:
        raise RuntimeError(f"{url}: no SERVER_DATA; has the site changed?")
    return json.loads(match.group(1)).get("NodeLoader", {}).get("node") or {}


def list_vacancies() -> tuple[dict[str, dict[str, Any]], int]:
    found: dict[str, dict[str, Any]] = {}
    total = 0
    for page in range(1, MAX_PAGES + 1):
        node = _node(LIST_URL if page == 1 else f"{LIST_URL}?page={page}")
        listing = next((item for item in node.get("lists") or [] if item.get("id") == "vacancies"), {})
        total = int(listing.get("totalItems") or total)
        items = listing.get("items") or []
        for item in items:
            found.setdefault(str(item.get("id")), item)
        if not items or len(found) >= total:
            break
    return found, total


def _date(timestamp: Any) -> str:
    if not timestamp:
        return ""
    return datetime.fromtimestamp(int(timestamp), TIME_ZONE).date().isoformat()


def fetch_vacancy(alias: str) -> dict[str, Any]:
    try:
        return _node(f"{SITE}{alias}")
    except RuntimeError as exc:
        print(f"  ! no detail for {alias}: {exc}", file=sys.stderr)
        return {}


def build_record(item: dict[str, Any], vacancy: dict[str, Any]) -> dict[str, Any]:
    merged = {**item, **vacancy}
    return {
        "job_id": merged.get("referenceNumber") or str(merged.get("id")),
        "title": str(merged.get("title") or "").strip(),
        # Every Europol post is at its headquarters.
        "location": "The Hague, Netherlands",
        "contract": merged.get("contractType", ""),
        "post_type": merged.get("postType", ""),
        "department": merged.get("department", ""),
        "reports_to": merged.get("reportTo", ""),
        "published": _date(merged.get("published")),
        "deadline": _date(merged.get("deadline")),
        "url": f"{SITE}{merged.get('alias', '')}",
        "apply_url": merged.get("applicationUrl", ""),
        "description": html_to_text(str(merged.get("body") or "")),
    }


def main() -> int:
    argparse.ArgumentParser(description=f"Extract every {NAME} open vacancy.").parse_args()

    started = datetime.now()
    print(f"{NAME} — every vacancy on {LIST_URL}")
    listing, total = list_vacancies()
    print(f"  {len(listing)} vacancies listed, page states {total}")

    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        vacancies = list(pool.map(fetch_vacancy, (item.get("alias", "") for item in listing.values())))

    return finish_run(
        name=NAME,
        out_dir=OUT_DIR,
        query="",
        source=LIST_URL,
        started=started,
        total=total,
        records=[build_record(item, vacancy) for item, vacancy in zip(listing.values(), vacancies)],
        missing_detail=sum(1 for vacancy in vacancies if not vacancy.get("body")),
    )


if __name__ == "__main__":
    sys.exit(main())

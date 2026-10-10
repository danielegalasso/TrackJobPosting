#!/usr/bin/env python3
"""Every open vacancy of the EU Satellite Centre (SatCen).

    https://recruitment.satcen.europa.eu/vacancies

An AngularJS app over one JSON endpoint, which returns every vacancy SatCen
has ever published, advert included:

    GET https://recruitment.satcen.europa.eu/api/Vacancy/

The page itself decides what is open: a vacancy whose `expireOn` has not
passed goes under "Open vacancies", the rest under closed ones. The same
rule is applied here, so the run shows what the page shows. Each vacancy
also carries a `status` field (OPEN / ONGOING / CLOSED), kept as a column.

What is handled here:
  * The page states no count, so the run checks itself against the endpoint:
    every vacancy it returns that has not expired must be extracted.
  * The advert comes as separate fields (description, responsibilities,
    qualifications, skills, requirements, conditions); they are joined under
    headings.

Output and the completeness check are in extract_common.py.
Usage:  python3 extractSatCenPositions.py [--all]
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from extract_common import finish_run, html_to_text, request_json

NAME = "SatCen"
SITE = "https://recruitment.satcen.europa.eu"
LIST_URL = f"{SITE}/vacancies"
API_URL = f"{SITE}/api/Vacancy/"
OUT_DIR = Path(__file__).resolve().parent / "SatCen"
#: Advert fields, in the order the vacancy page shows them, with a heading each.
_SECTIONS = (
    ("description", ""),
    ("qualifications", "Qualifications"),
    ("skills", "Skills"),
    ("requirements", "Eligibility"),
    ("conditions", "Conditions"),
)


def _moment(value: Any) -> datetime | None:
    if not value:
        return None
    moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _advert(vacancy: dict[str, Any]) -> str:
    parts = []
    for key, heading in _SECTIONS:
        body = html_to_text(str(vacancy.get(key) or "")).strip()
        if body:
            parts.append(f"{heading}\n{body}" if heading else body)
    return "\n\n".join(parts)


def build_record(vacancy: dict[str, Any]) -> dict[str, Any]:
    expires = _moment(vacancy.get("expireOn"))
    published = _moment(vacancy.get("publishedOn"))
    return {
        "job_id": vacancy.get("reference") or vacancy.get("id"),
        "title": str(vacancy.get("title") or "").strip(),
        # SatCen has one site, at Torrejón de Ardoz.
        "location": "Torrejón de Ardoz, Spain",
        "contract": vacancy.get("typePost", ""),
        "status": vacancy.get("status", ""),
        "unit": vacancy.get("workUnit", ""),
        "reports_to": vacancy.get("responsibleto", ""),
        "internal_only": vacancy.get("internalOnly", ""),
        "published": published.date().isoformat() if published else "",
        "deadline": expires.date().isoformat() if expires else "",
        "url": f"{SITE}/vacancy/{vacancy.get('id')}",
        "description": _advert(vacancy),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=f"Extract {NAME} vacancies.")
    parser.add_argument("--all", action="store_true", help="every vacancy ever published, not only open ones")
    take_all = parser.parse_args().all

    started = datetime.now()
    now = datetime.now(timezone.utc)
    print(f"{NAME} — {'every' if take_all else 'open'} vacancy on {LIST_URL}")
    vacancies = request_json(API_URL)
    chosen = [
        vacancy
        for vacancy in vacancies
        if take_all or ((_moment(vacancy.get("expireOn")) or now) >= now)
    ]
    print(f"  {len(vacancies)} vacancies on record, {len(chosen)} {'taken' if take_all else 'still open'}")

    records = {}
    for vacancy in chosen:
        records.setdefault(str(vacancy.get("id")), build_record(vacancy))
    return finish_run(
        name=NAME,
        out_dir=OUT_DIR,
        query="all" if take_all else "open",
        source=LIST_URL,
        started=started,
        total=len(chosen),
        records=list(records.values()),
        missing_detail=sum(1 for record in records.values() if not record["description"]),
    )


if __name__ == "__main__":
    sys.exit(main())

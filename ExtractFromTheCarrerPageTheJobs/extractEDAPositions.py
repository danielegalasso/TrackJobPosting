#!/usr/bin/env python3
"""Every European Defence Agency vacancy.

    https://eda.europa.eu/careers/current-vacancies

The careers page lists each vacancy as a card linking to the agency's
recruitment portal, vacancies.eda.europa.eu/vacanciesnotice/<id>. The cards
are in the HTML but hidden (`display:none`) until the page's script shows
them, which is also why "There are no vacancies" is in the source: it is the
fallback message, hidden as well. The portal is an Angular app over a JSON
API, the one its own pages call:

    GET https://vacancies.eda.europa.eu/api/public/Vacancies/PublishedVacancies
    GET https://vacancies.eda.europa.eu/api/public/Vacancies/GetVacancyAndRelatedNotices/<id>

The second carries reference, grade, directorate, security clearance, dates
and the advert as titled sections ("Notices").

What is handled here:
  * The portal's server sends its certificate without the GlobalSign
    intermediate that links it to a trusted root. The intermediate is fetched
    from the address in the certificate, as browsers do, and verification
    stays on.
  * The page's cards and the API's list are two renderings of the same set;
    each is checked against the other, and every vacancy either names is
    extracted.

Output and the completeness check are in extract_common.py.
Usage:  python3 extractEDAPositions.py
"""

from __future__ import annotations

import argparse
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from extract_common import (
    DETAIL_WORKERS,
    finish_run,
    html_to_text,
    request_json,
    request_text,
    trust_intermediate,
)

NAME = "EDA"
CAREERS_URL = "https://eda.europa.eu/careers/current-vacancies"
PORTAL = "https://vacancies.eda.europa.eu"
API = f"{PORTAL}/api/public/Vacancies"
OUT_DIR = Path(__file__).resolve().parent / "EDA"
#: Where GlobalSign publishes the intermediate the portal leaves out; the
#: address is the CA Issuers entry of the portal's own certificate.
INTERMEDIATE_CA = "http://secure.globalsign.com/cacert/gsgccr3dvtlsca2020.crt"

_NOTICE_RE = re.compile(r"vacancies\.eda\.europa\.eu/vacanciesnotice/(\d+)")


def _date(value: Any) -> str:
    return str(value or "")[:10]


def list_vacancies() -> tuple[list[str], dict[str, dict[str, Any]]]:
    """Vacancy ids named by the careers page or the API, and the API's entries."""
    page_ids = set(_NOTICE_RE.findall(request_text(CAREERS_URL)))
    published = {str(item.get("IDVacanciesVersion")): item for item in request_json(f"{API}/PublishedVacancies")}
    print(f"  {len(page_ids)} vacancies on the careers page, {len(published)} published on the portal")
    for label, ids in (("careers page", page_ids - published.keys()), ("portal", published.keys() - page_ids)):
        if ids:
            print(f"  ! only on the {label}: {', '.join(sorted(ids))}", file=sys.stderr)
    return sorted(page_ids | published.keys(), key=int), published


def fetch_vacancy(vacancy_id: str) -> dict[str, Any]:
    try:
        data = request_json(f"{API}/GetVacancyAndRelatedNotices/{vacancy_id}")
    except RuntimeError as exc:
        print(f"  ! no detail for {vacancy_id}: {exc}", file=sys.stderr)
        return {}
    return data if isinstance(data, dict) else {}


def _advert(vacancy: dict[str, Any]) -> str:
    sections = sorted(vacancy.get("Notices") or [], key=lambda notice: notice.get("Order") or 0)
    parts = []
    for notice in sections:
        body = html_to_text(str(notice.get("Description") or ""))
        if body:
            label = str(notice.get("Label") or "").strip()
            parts.append(f"{label}\n{body}" if label else body)
    return "\n\n".join(parts)


def build_record(vacancy_id: str, listed: dict[str, Any], vacancy: dict[str, Any]) -> dict[str, Any]:
    merged = {**listed, **vacancy}
    return {
        "job_id": merged.get("Reference") or vacancy_id,
        "title": str(merged.get("FrendlyTitleFrontend") or merged.get("Post") or "").strip(),
        "location": merged.get("Location", ""),
        "contract": merged.get("ContractStatusText", ""),
        "grade": merged.get("JobGrade") or merged.get("Grade") or "",
        "directorate": merged.get("Directorate", ""),
        "security_clearance": merged.get("SecurityClearance", ""),
        "published": _date(merged.get("PublicationDateStart")),
        # An extended deadline replaces the original one in PublicationDateEndShow.
        "deadline": _date(merged.get("PublicationDateEndShow") or merged.get("PublicationDateEnd")),
        "job_start": merged.get("StartingDateText") if merged.get("StartingDateTextShow") else _date(merged.get("StartingDate")),
        "url": f"{PORTAL}/vacanciesnotice/{vacancy_id}",
        "description": _advert(vacancy),
    }


def main() -> int:
    argparse.ArgumentParser(description=f"Extract every {NAME} vacancy.").parse_args()

    started = datetime.now()
    print(f"{NAME} — every vacancy on {CAREERS_URL}")
    try:
        trust_intermediate(INTERMEDIATE_CA)
    except OSError as exc:
        # Not fatal: if EDA has since fixed its server, the chain is complete
        # without it. If not, the portal requests below will say so.
        print(f"  ! could not fetch the intermediate certificate ({exc}); trying without it", file=sys.stderr)
    ids, published = list_vacancies()

    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        vacancies = list(pool.map(fetch_vacancy, ids))

    return finish_run(
        name=NAME,
        out_dir=OUT_DIR,
        query="",
        source=CAREERS_URL,
        started=started,
        total=len(ids),
        records=[
            build_record(vacancy_id, published.get(vacancy_id, {}), vacancy)
            for vacancy_id, vacancy in zip(ids, vacancies)
        ],
        missing_detail=sum(1 for vacancy in vacancies if not vacancy.get("Notices")),
    )


if __name__ == "__main__":
    sys.exit(main())

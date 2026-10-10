"""Extract every posting on a Pinpoint career site.

A Pinpoint careers page such as
    https://nccgroup.pinpointhq.com/#js-careers-jobs-block
holds no postings in its HTML: its script fills the jobs block from

    GET https://<company>.pinpointhq.com/postings.json

which returns every live posting in one response, advert included, so there
is no pagination to get wrong and no per-posting request to make.

What is handled here:
  * The feed has no total of its own, and the page shows nothing a plain
    fetch can count, so the run checks the feed against `expected` when one
    is given (the count a person read off the page), and otherwise reports
    the feed's length as the site's total.
  * A Pinpoint advert is split into sections — description, key
    responsibilities, skills, benefits — each with its own header, and a
    section left as a placeholder ("." or empty) is not shown on the page.
    The description column joins the sections that are, under their headers.
  * One requisition is often published several times, once per office, and
    occasionally twice for the same office. Each is its own posting with its
    own URL on the page, so postings are told apart by URL, not requisition.
  * Locations are named "<ISO 3166 alpha-3> <office>", e.g. "GBR London", so
    the country is the leading code.

Output and the completeness check are in extract_common.py. Each company has
a small entry script that names its site and calls `main`.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from extract_common import finish_run, html_to_text, request_json

#: Advert sections after the description, in the order the page shows them.
_SECTIONS = ("key_responsibilities", "skills_knowledge_expertise", "benefits")
_COUNTRY_CODE_RE = re.compile(r"^([A-Z]{3})\b")


@dataclass(frozen=True)
class Site:
    """One Pinpoint career site."""

    name: str
    host: str
    out_dir: Path
    #: How many postings the page showed when the extractor was written.
    #: Only used to warn if the feed disagrees; the site has no total.
    expected: int | None = None

    @property
    def feed_url(self) -> str:
        return f"https://{self.host}/postings.json"


def _advert(posting: dict[str, Any]) -> str:
    parts = [html_to_text(posting.get("description") or "")]
    for section in _SECTIONS:
        body = html_to_text(posting.get(section) or "")
        if len(body.strip(" .")) < 2:
            continue
        header = (posting.get(f"{section}_header") or "").strip(" .")
        parts.append(f"{header}\n{body}" if header else body)
    return "\n\n".join(part for part in parts if part)


def _compensation(posting: dict[str, Any]) -> str:
    if not posting.get("compensation_visible"):
        return ""
    if posting.get("compensation"):
        return str(posting["compensation"])
    low, high = posting.get("compensation_minimum"), posting.get("compensation_maximum")
    span = "–".join(str(value) for value in (low, high) if value)
    unit = " ".join(
        str(value)
        for value in (posting.get("compensation_currency"), posting.get("compensation_frequency"))
        if value
    )
    return f"{span} {unit}".strip()


def build_record(site: Site, posting: dict[str, Any]) -> dict[str, Any]:
    job = posting.get("job") or {}
    location = posting.get("location") or {}
    name = str(location.get("name") or "")
    code = _COUNTRY_CODE_RE.match(name)
    place = ", ".join(
        str(part) for part in (location.get("city"), location.get("province")) if part
    )
    return {
        "job_id": job.get("requisition_id") or str(posting.get("id") or ""),
        "title": str(posting.get("title") or "").strip(),
        "location": place or name,
        "office": name,
        "country": code.group(1) if code else "",
        "time_type": posting.get("employment_type_text", ""),
        "remote_type": posting.get("workplace_type_text", ""),
        "department": (job.get("department") or {}).get("name", ""),
        "compensation": _compensation(posting),
        "deadline": str(posting.get("deadline_at") or "")[:10],
        "url": posting.get("url") or f"https://{site.host}{posting.get('path', '')}",
        "description": _advert(posting),
    }


def main(site: Site) -> int:
    argparse.ArgumentParser(description=f"Extract every {site.name} posting on its Pinpoint site.").parse_args()

    started = datetime.now()
    print(f"{site.name} Pinpoint — every posting")
    postings = request_json(site.feed_url).get("data") or []
    records = {}
    for posting in postings:
        record = build_record(site, posting)
        records.setdefault(record["url"], record)
    print(f"  {len(postings)} postings in the feed, {len(records)} distinct")

    total = len(postings)
    if site.expected is not None and site.expected != total:
        print(
            f"  note: the page showed {site.expected} when this extractor was set up; "
            f"the feed now has {total}"
        )

    return finish_run(
        name=site.name,
        out_dir=site.out_dir,
        # Pinpoint has no search; the whole board is the result.
        query="",
        source=f"https://{site.host}/",
        started=started,
        total=total,
        records=list(records.values()),
        missing_detail=sum(1 for record in records.values() if not record["description"]),
    )

"""Extract every posting on a rexx systems career site.

A rexx job portal such as
    https://jobs.secunet.com/stellenangebote.html
renders its whole list server-side, one `joboffer_container` per posting:
the title links to `<slug>-<lang>-j<id>.html`, followed by a few
`joboffer_informations` boxes whose meaning depends on how the company set
the portal up (secunet: division, contract type, locations). The same
postings are published as an Atom feed,

    GET https://<host>/rss_generator-rss0.php?unit=<unit>&lang=<lang>

(named rss, but Atom), and each posting page carries a schema.org
JobPosting in JSON-LD with the advert split into description,
responsibilities, qualifications and benefits.

What is handled here:
  * Neither the page nor the feed states a total. They are two independent
    renderings of the same list, so each is checked against the other, and
    the run counts as complete only when every posting either one names has
    been extracted.
  * The JSON-LD is written by PHP with raw newlines inside strings, which
    strict JSON rejects, so it is parsed leniently.
  * secunet's `description` already holds the whole advert, sections and
    all; a section is appended under its own heading only when the
    description does not contain it, so no portal's advert is doubled.
  * The JSON-LD `employmentType` says FULL_TIME even for student jobs, so it
    is left out; the list page's own contract box is the reliable one.

Output and the completeness check are in extract_common.py. Each company has
a small entry script that names its portal and calls `main`.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from extract_common import DETAIL_WORKERS, finish_run, html_to_text, request_text

_ATOM = "{http://www.w3.org/2005/Atom}"
_CONTAINER_RE = re.compile(r'<div class="joboffer_container">(.*?)(?=<div class="joboffer_container">|</main>|$)', re.S)
_TITLE_RE = re.compile(r'<a[^>]+href="([^"]+-j(\d+)\.html)"[^>]*>(.*?)</a>', re.S)
_INFO_RE = re.compile(r'<div class="joboffer_informations joboffer_box">(.*?)</div>', re.S)
_JSON_LD_RE = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
_ID_RE = re.compile(r"-j(\d+)\.html")
#: JSON-LD advert sections after the description, with the heading each gets.
_SECTIONS = (
    ("responsibilities", "Responsibilities"),
    ("qualifications", "Qualifications"),
    ("jobBenefits", "Benefits"),
)


@dataclass(frozen=True)
class Site:
    """One rexx job portal."""

    name: str
    host: str
    list_path: str
    #: The feed's `unit` parameter, as the portal's own feed link has it.
    unit: str
    lang: str
    out_dir: Path
    #: What each joboffer_informations box on the list holds, in order.
    info_columns: tuple[str, ...] = ()

    @property
    def list_url(self) -> str:
        return f"https://{self.host}/{self.list_path}"

    @property
    def feed_url(self) -> str:
        return f"https://{self.host}/rss_generator-rss0.php?unit={self.unit}&lang={self.lang}"


def _clean(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def list_page(site: Site) -> dict[str, dict[str, Any]]:
    """The postings the list page renders, keyed by rexx job id."""
    page = request_text(site.list_url)
    found: dict[str, dict[str, Any]] = {}
    for block in _CONTAINER_RE.findall(page):
        title = _TITLE_RE.search(block)
        if not title:
            continue
        url, job_id, text = title.groups()
        boxes = [_clean(box) for box in _INFO_RE.findall(block)]
        entry = {"url": html.unescape(url), "title": _clean(text)}
        entry.update(zip(site.info_columns, boxes))
        found.setdefault(job_id, entry)
    return found


def list_feed(site: Site) -> dict[str, dict[str, Any]]:
    """The postings the Atom feed carries, keyed by rexx job id."""
    try:
        root = ET.fromstring(request_text(site.feed_url))
    except (RuntimeError, ET.ParseError) as exc:
        print(f"  ! feed unreadable, checking against the page alone: {exc}", file=sys.stderr)
        return {}
    found: dict[str, dict[str, Any]] = {}
    for entry in root.iter(f"{_ATOM}entry"):
        link = entry.find(f"{_ATOM}link")
        url = link.get("href", "") if link is not None else ""
        match = _ID_RE.search(url)
        if not match:
            continue
        category = entry.find(f"{_ATOM}category")
        found.setdefault(
            match.group(1),
            {
                "url": url,
                "title": (entry.findtext(f"{_ATOM}title") or "").strip(),
                "feed_category": category.get("term", "") if category is not None else "",
            },
        )
    return found


def fetch_detail(url: str) -> dict[str, Any]:
    """The posting page's schema.org JobPosting."""
    try:
        page = request_text(url)
    except RuntimeError as exc:
        print(f"  ! no detail for {url}: {exc}", file=sys.stderr)
        return {}
    for block in _JSON_LD_RE.findall(page):
        try:
            data = json.loads(block, strict=False)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("@type") == "JobPosting":
            return data
    print(f"  ! no JobPosting data on {url}", file=sys.stderr)
    return {}


def _letters(text: str) -> str:
    return re.sub(r"\W+", "", text).lower()


def _advert(posting: dict[str, Any]) -> str:
    parts = [html_to_text(str(posting.get("description") or ""))]
    for key, heading in _SECTIONS:
        body = html_to_text(str(posting.get(key) or ""))
        if body and _letters(body) not in _letters(parts[0]):
            parts.append(f"{heading}\n{body}")
    return "\n\n".join(part for part in parts if part)


def _places(posting: dict[str, Any]) -> list[tuple[str, str]]:
    places = posting.get("jobLocation") or []
    if isinstance(places, dict):
        places = [places]
    found = []
    for place in places:
        address = (place or {}).get("address") or {}
        found.append((str(address.get("addressLocality") or ""), str(address.get("addressCountry") or "")))
    return found


def build_record(site: Site, job_id: str, entry: dict[str, Any], posting: dict[str, Any]) -> dict[str, Any]:
    places = _places(posting)
    countries = list(dict.fromkeys(country for _, country in places if country))
    record: dict[str, Any] = {
        "job_id": job_id,
        "title": html.unescape(str(posting.get("title") or entry.get("title") or "")).strip(),
        # The list box names every site; the JSON-LD only those with an address.
        "location": entry.get("location") or ", ".join(city for city, _ in places if city),
        "country": ", ".join(countries),
    }
    for column in site.info_columns:
        if column != "location":
            record[column] = entry.get(column, "")
    record.update(
        {
            "start_date": str(posting.get("datePosted") or "")[:10],
            "valid_through": str(posting.get("validThrough") or "")[:10],
            "url": entry["url"],
            "description": _advert(posting),
        }
    )
    return record


def main(site: Site) -> int:
    argparse.ArgumentParser(description=f"Extract every {site.name} posting on its rexx portal.").parse_args()

    started = datetime.now()
    print(f"{site.name} rexx — every posting on {site.list_url}")
    page = list_page(site)
    feed = list_feed(site)
    print(f"  {len(page)} postings on the page, {len(feed)} in the feed")
    for label, ids in (("page", page.keys() - feed.keys()), ("feed", feed.keys() - page.keys())):
        if feed and ids:
            print(f"  ! only on the {label}: {', '.join(sorted(ids))}", file=sys.stderr)
    # A posting either rendering names is a posting; the page's entry has
    # more fields, so it wins where both have one.
    listing = {**feed, **page}

    print(f"  fetching {len(listing)} adverts…")
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        details = list(pool.map(fetch_detail, (entry["url"] for entry in listing.values())))

    return finish_run(
        name=site.name,
        out_dir=site.out_dir,
        # The portal is taken whole; there is no search.
        query="",
        source=site.list_url,
        started=started,
        total=len(listing),
        records=[
            build_record(site, job_id, entry, posting)
            for (job_id, entry), posting in zip(listing.items(), details)
        ],
        missing_detail=sum(1 for posting in details if not posting),
    )

"""Job boards whose platform publishes the whole list through a public API.

One function per platform, each taking a board's entry from boards.json and
returning a Collected: the postings as records, and the total to check them
against. The entries' fields per platform are documented on each function.

Most of these platforms answer with every posting at once, so there is no
pager to get wrong. Where a platform has no search of its own and the user's
link carried one (`query`), the postings are narrowed here: every word of the
query in the title or the advert.

Standard library only.
"""

from __future__ import annotations

import html
import json
import re
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from extract_common import (
    DETAIL_WORKERS,
    Collected,
    html_to_text,
    inner_html,
    json_ld_job,
    json_ld_place,
    matches_query,
    request_json,
    request_text,
)


def _iso(value: Any) -> str:
    """A date from an ISO string or a millisecond timestamp, as YYYY-MM-DD."""
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, timezone.utc).date().isoformat()
    match = re.match(r"\d{4}-\d{2}-\d{2}", str(value))
    return match.group(0) if match else ""


def _narrow(cfg: dict[str, Any], records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    query = (cfg.get("query") or "").strip()
    if not query:
        return records, []
    kept = [r for r in records if matches_query(query, r["title"], r.get("description", ""))]
    return kept, [f'narrowed to "{query}": {len(kept)} of {len(records)}']


def _detail_pool(function, items):
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        return list(pool.map(function, items))


# ----------------------------------------------------------------- Greenhouse


def greenhouse(cfg: dict[str, Any]) -> Collected:
    """`board`: the board token (job-boards.greenhouse.io/<board>); optional
    `department`, the one department a link's team filter keeps (Adyen's
    ?team=Security), and `locations`, the places a link's location filter
    keeps (any of them in the posting's location)."""
    from greenhouse_extract import build_record

    payload = request_json(f"https://boards-api.greenhouse.io/v1/boards/{cfg['board']}/jobs?content=true")
    jobs = payload.get("jobs") or []
    notes = []
    if cfg.get("department"):
        every = len(jobs)
        jobs = [
            job for job in jobs
            if any(d.get("name") == cfg["department"] for d in job.get("departments") or [])
        ]
        notes.append(f'department "{cfg["department"]}": {len(jobs)} of {every}')
    if cfg.get("locations"):
        every = len(jobs)
        wanted = [place.lower() for place in cfg["locations"]]
        jobs = [
            job for job in jobs
            if any(place in str((job.get("location") or {}).get("name") or "").lower() for place in wanted)
        ]
        notes.append(f'in {", ".join(cfg["locations"])}: {len(jobs)} of {every}')
    records = [build_record(job) for job in jobs]
    kept, narrowed = _narrow(cfg, records)
    notes += narrowed
    total = int((payload.get("meta") or {}).get("total") or len(jobs))
    return Collected(kept, len(kept) if notes else total, notes=notes, confirmed_empty=True)


# ---------------------------------------------------------------------- Ashby


def ashby(cfg: dict[str, Any]) -> Collected:
    """`board`: the organisation's handle (jobs.ashbyhq.com/<board>)."""
    payload = request_json(
        f"https://api.ashbyhq.com/posting-api/job-board/{cfg['board']}?includeCompensation=true"
    )
    records = []
    for job in payload.get("jobs") or []:
        if job.get("isListed") is False:
            continue
        address = ((job.get("address") or {}).get("postalAddress") or {})
        extra = [place.get("location", "") for place in job.get("secondaryLocations") or []]
        records.append(
            {
                "job_id": job.get("id", ""),
                "title": str(job.get("title") or "").strip(),
                "location": job.get("location", ""),
                "additional_locations": "; ".join(place for place in extra if place),
                "country": address.get("addressCountry", ""),
                "department": job.get("department") or job.get("team") or "",
                "contract": job.get("employmentType", ""),
                "remote_type": job.get("workplaceType") or ("Remote" if job.get("isRemote") else ""),
                "compensation": ((job.get("compensation") or {}).get("compensationTierSummary") or ""),
                "published": _iso(job.get("publishedAt")),
                "url": job.get("jobUrl", ""),
                "description": job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml") or ""),
            }
        )
    kept, notes = _narrow(cfg, records)
    return Collected(kept, len(kept), notes=notes, confirmed_empty=True)


# ---------------------------------------------------------------------- Lever


def lever(cfg: dict[str, Any]) -> Collected:
    """`board`: the company handle (jobs.lever.co/<board>); `region`: "eu" for jobs.eu.lever.co."""
    host = "api.eu.lever.co" if cfg.get("region") == "eu" else "api.lever.co"
    postings = request_json(f"https://{host}/v0/postings/{cfg['board']}?mode=json")
    records = []
    for job in postings:
        categories = job.get("categories") or {}
        # Some boards send the plain-text copies empty and only the HTML
        # (Tiberius), so the HTML stands in for them.
        sections = [job.get("descriptionPlain") or html_to_text(job.get("description") or "")]
        for item in job.get("lists") or []:
            sections.append(f"{item.get('text', '')}\n{html_to_text(item.get('content') or '')}")
        sections.append(job.get("additionalPlain") or html_to_text(job.get("additional") or ""))
        records.append(
            {
                "job_id": job.get("id", ""),
                "title": str(job.get("text") or "").strip(),
                "location": categories.get("location", ""),
                "additional_locations": "; ".join(categories.get("allLocations") or [])
                if len(categories.get("allLocations") or []) > 1
                else "",
                "country": job.get("country", ""),
                "department": categories.get("team") or categories.get("department") or "",
                "contract": categories.get("commitment", ""),
                "remote_type": job.get("workplaceType", ""),
                "published": _iso(job.get("createdAt")),
                "url": job.get("hostedUrl", ""),
                "description": "\n\n".join(part.strip() for part in sections if part and part.strip()),
            }
        )
    kept, notes = _narrow(cfg, records)
    return Collected(kept, len(kept), notes=notes, confirmed_empty=True)


# ------------------------------------------------------------------- Workable


def workable(cfg: dict[str, Any]) -> Collected:
    """`board`: the account (apply.workable.com/<board>)."""
    payload = request_json(f"https://apply.workable.com/api/v1/widget/accounts/{cfg['board']}?details=true")
    records = []
    for job in payload.get("jobs") or []:
        places = [
            ", ".join(part for part in (place.get("city"), place.get("country")) if part)
            for place in job.get("locations") or []
        ]
        records.append(
            {
                "job_id": job.get("shortcode", ""),
                "title": str(job.get("title") or "").strip(),
                "location": places[0] if places else job.get("country", ""),
                "additional_locations": "; ".join(places[1:]),
                "country": job.get("country", ""),
                "department": job.get("department", ""),
                "contract": job.get("employment_type", ""),
                "remote_type": "Remote" if job.get("telecommuting") else "",
                "published": _iso(job.get("published_on") or job.get("created_at")),
                "url": job.get("url") or job.get("shortlink") or "",
                "description": html_to_text(job.get("description") or ""),
            }
        )
    kept, notes = _narrow(cfg, records)
    return Collected(kept, len(kept), notes=notes, confirmed_empty=True)


# ------------------------------------------------------------------- Personio

_PERSONIO_LINK_RE = re.compile(r'href="((?:https://[a-z0-9-]+\.jobs\.personio\.(?:de|com))?/job/(\d+)[^"]*)"')


def personio(cfg: dict[str, Any]) -> Collected:
    """`board`: the company (<board>.jobs.personio.com); `domain`: "de" or "com" (default com).

    Personio's XML feed answers 404 for these companies and its posting pages
    no longer carry a JobPosting; the job list links every posting, and each
    posting page is rendered server-side with stable class names
    (job-position-title, jb-attribute, jb-description-item).
    """
    base = f"https://{cfg['board']}.jobs.personio.{cfg.get('domain', 'com')}"
    language = cfg.get("language", "en")
    page = request_text(f"{base}/?language={language}")
    ids = list(dict.fromkeys(job_id for _, job_id in _PERSONIO_LINK_RE.findall(page)))

    def detail(job_id: str) -> dict[str, Any]:
        url = f"{base}/job/{job_id}?language={language}"
        try:
            page = request_text(url)
        except RuntimeError as exc:
            print(f"  ! no detail for {url}: {exc}", file=sys.stderr)
            page = ""
        title = html_to_text(next(iter(inner_html(page, "class", "job-position-title", token=True)), ""))
        attributes = [
            line
            for line in html_to_text(next(iter(inner_html(page, "class", "jb-attribute", token=True)), "")).splitlines()
            if line.strip()
        ]
        advert = "\n\n".join(
            html_to_text(block) for block in inner_html(page, "class", "jb-description-item", token=True)
        )
        return {
            "job_id": job_id,
            "title": title,
            "location": attributes[0] if attributes else "",
            "contract": " · ".join(attributes[1:]),
            "url": f"{base}/job/{job_id}",
            "description": advert,
        }

    records = [record for record in _detail_pool(detail, ids) if record["title"]]
    kept, notes = _narrow(cfg, records)
    return Collected(
        kept,
        len(kept) if notes else len(ids),
        sum(1 for r in records if not r["description"]),
        notes,
        confirmed_empty=bool(ids) or "no positions" in page.lower(),
    )


# ------------------------------------------------------------------ Recruitee


def recruitee(cfg: dict[str, Any]) -> Collected:
    """`board`: the company (<board>.recruitee.com), or `host` for a custom domain."""
    host = cfg.get("host") or f"{cfg['board']}.recruitee.com"
    payload = request_json(f"https://{host}/api/offers/")
    records = []
    for offer in payload.get("offers") or []:
        if offer.get("status") not in (None, "published"):
            continue
        description = "\n\n".join(
            html_to_text(offer.get(key) or "") for key in ("description", "requirements") if offer.get(key)
        )
        records.append(
            {
                "job_id": str(offer.get("id") or offer.get("slug") or ""),
                "title": str(offer.get("title") or "").strip(),
                "location": offer.get("location") or offer.get("city") or "",
                "country": offer.get("country", ""),
                "department": offer.get("department") or "",
                "contract": offer.get("employment_type_code", ""),
                "remote_type": "Remote" if offer.get("remote") else "",
                "published": _iso(offer.get("published_at") or offer.get("created_at")),
                "deadline": _iso(offer.get("close_at")),
                "url": offer.get("careers_url") or offer.get("url") or "",
                "description": description,
            }
        )
    kept, notes = _narrow(cfg, records)
    return Collected(kept, len(kept), notes=notes, confirmed_empty=True)


# ----------------------------------------------------------------- Teamtailor

_TT = "{https://teamtailor.com/locations}"


def teamtailor(cfg: dict[str, Any]) -> Collected:
    """`host`: the career site's host (careers.example.com or <x>.teamtailor.com).

    Every Teamtailor site, custom domain or not, publishes /jobs.rss with
    every public posting and its advert.
    """
    feed = ET.fromstring(request_text(f"https://{cfg['host']}/jobs.rss"))
    records = []
    for item in feed.iter("item"):
        places = []
        for location in item.iter(f"{_TT}location"):
            city = (location.findtext(f"{_TT}city") or "").strip()
            country = (location.findtext(f"{_TT}country") or "").strip()
            places.append(", ".join(part for part in (city, country) if part))
        link = (item.findtext("link") or "").strip()
        published = item.findtext("pubDate") or ""
        try:
            published = datetime.strptime(published.strip(), "%a, %d %b %Y %H:%M:%S %z").date().isoformat()
        except ValueError:
            published = ""
        records.append(
            {
                "job_id": (item.findtext("guid") or link).rsplit("/", 1)[-1],
                "title": (item.findtext("title") or "").strip(),
                "location": places[0] if places else "",
                "additional_locations": "; ".join(places[1:]),
                "department": (item.findtext(f"{_TT}department") or "").strip(),
                "remote_type": (item.findtext("remoteStatus") or "").strip(),
                "published": published,
                "url": link,
                "description": html_to_text(html.unescape(item.findtext("description") or "")),
            }
        )
    kept, notes = _narrow(cfg, records)
    return Collected(kept, len(kept), notes=notes, confirmed_empty=True)


# ------------------------------------------------------------------- BambooHR


def bamboohr(cfg: dict[str, Any]) -> Collected:
    """`board`: the company (<board>.bamboohr.com)."""
    base = f"https://{cfg['board']}.bamboohr.com/careers"
    payload = request_json(f"{base}/list")
    jobs = payload.get("result") or []

    def detail(job: dict[str, Any]) -> dict[str, Any]:
        job_id = str(job.get("id"))
        try:
            opening = (request_json(f"{base}/{job_id}/detail").get("result") or {}).get("jobOpening") or {}
        except RuntimeError as exc:
            print(f"  ! no detail for {job_id}: {exc}", file=sys.stderr)
            opening = {}
        location = job.get("location") or {}
        return {
            "job_id": job_id,
            "title": str(job.get("jobOpeningName") or "").strip(),
            "location": ", ".join(part for part in (location.get("city"), location.get("state")) if part),
            "country": location.get("country") or "",
            "department": job.get("departmentLabel") or "",
            "contract": job.get("employmentStatusLabel") or "",
            "remote_type": job.get("locationType") or "",
            "published": _iso(opening.get("datePosted")),
            "url": f"{base}/{job_id}",
            "description": html_to_text(opening.get("description") or ""),
        }

    records = _detail_pool(detail, jobs)
    kept, notes = _narrow(cfg, records)
    total = int((payload.get("meta") or {}).get("totalCount") or len(jobs))
    return Collected(
        kept,
        len(kept) if notes else total,
        sum(1 for r in records if not r["description"]),
        notes,
        confirmed_empty=True,
    )


# --------------------------------------------------------------------- Breezy


def breezy(cfg: dict[str, Any]) -> Collected:
    """`board`: the company (<board>.breezy.hr). Adverts come from each page's JobPosting."""
    jobs = request_json(f"https://{cfg['board']}.breezy.hr/json")

    def detail(job: dict[str, Any]) -> dict[str, Any]:
        url = job.get("url") or f"https://{cfg['board']}.breezy.hr/p/{job.get('friendly_id')}"
        try:
            page = request_text(url)
        except RuntimeError as exc:
            print(f"  ! no detail for {url}: {exc}", file=sys.stderr)
            page = ""
        posting = json_ld_job(page)
        # Open applications carry no JobPosting; their text is in the page.
        advert = str(posting.get("description") or "") or next(
            iter(inner_html(page, "class", "description", token=True)), ""
        )
        location = job.get("location") or {}
        country = location.get("country") or {}
        return {
            "job_id": str(job.get("id") or ""),
            "title": str(job.get("name") or "").strip(),
            "location": location.get("name") or location.get("city") or "",
            "country": country.get("name", "") if isinstance(country, dict) else str(country),
            "department": job.get("department") or "",
            "contract": (job.get("type") or {}).get("name", "") if isinstance(job.get("type"), dict) else "",
            "remote_type": "Remote" if location.get("is_remote") else "",
            "published": _iso(job.get("published_date")),
            "url": url,
            "description": html_to_text(html.unescape(advert)),
        }

    records = _detail_pool(detail, jobs)
    kept, notes = _narrow(cfg, records)
    return Collected(
        kept, len(kept), sum(1 for r in records if not r["description"]), notes, confirmed_empty=True
    )


# ------------------------------------------------------------- SmartRecruiters


def smartrecruiters(cfg: dict[str, Any]) -> Collected:
    """`board`: the company id; optional `filters`: extra API filters, e.g. {"custom_field.<id>": "<value>"}."""
    base = f"https://api.smartrecruiters.com/v1/companies/{cfg['board']}/postings"
    extra = "".join(f"&{key}={value}" for key, value in (cfg.get("filters") or {}).items())
    query = f"&q={cfg['query']}" if cfg.get("query") else ""
    postings: list[dict[str, Any]] = []
    total = 0
    offset = 0
    while True:
        page = request_json(f"{base}?limit=100&offset={offset}{query}{extra}")
        total = int(page.get("totalFound") or 0)
        content = page.get("content") or []
        postings.extend(content)
        offset += len(content)
        if not content or offset >= total:
            break

    def detail(posting: dict[str, Any]) -> dict[str, Any]:
        try:
            full = request_json(f"{base}/{posting['id']}")
        except RuntimeError as exc:
            print(f"  ! no detail for {posting.get('id')}: {exc}", file=sys.stderr)
            full = {}
        sections = ((full.get("jobAd") or {}).get("sections") or {})
        text = "\n\n".join(
            f"{part.get('title', '')}\n{html_to_text(part.get('text') or '')}".strip()
            for part in sections.values()
            if isinstance(part, dict) and part.get("text")
        )
        location = posting.get("location") or {}
        return {
            "job_id": str(posting.get("id") or ""),
            "title": str(posting.get("name") or "").strip(),
            "location": ", ".join(part for part in (location.get("city"), location.get("region")) if part),
            "country": str(location.get("country") or "").upper(),
            "department": (posting.get("department") or {}).get("label", ""),
            "contract": (posting.get("typeOfEmployment") or {}).get("label", ""),
            "remote_type": "Remote" if location.get("remote") else "",
            "published": _iso(posting.get("releasedDate")),
            "url": full.get("postingUrl") or f"https://jobs.smartrecruiters.com/{cfg['board']}/{posting.get('id')}",
            "description": text,
        }

    records = _detail_pool(detail, postings)
    return Collected(
        records, total, sum(1 for r in records if not r["description"]), confirmed_empty=True
    )


PLATFORMS = {
    "greenhouse": greenhouse,
    "ashby": ashby,
    "lever": lever,
    "workable": workable,
    "personio": personio,
    "recruitee": recruitee,
    "teamtailor": teamtailor,
    "bamboohr": bamboohr,
    "breezy": breezy,
    "smartrecruiters": smartrecruiters,
}

if __name__ == "__main__":  # a quick look at one board: python3 ats_simple.py ashby '{"board":"x"}'
    result = PLATFORMS[sys.argv[1]](json.loads(sys.argv[2]))
    print(result.total, len(result.records), result.missing_detail, result.notes)
    for record in result.records[:3]:
        print({k: str(v)[:60] for k, v in record.items()})

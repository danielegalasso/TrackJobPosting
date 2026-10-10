"""Career sites without a platform of their own, and the small platforms.

`html_list` is the general case, driven entirely by the board's entry: a list
page whose links to postings match a pattern, followed (optionally) through
its pager, and each posting page read for its schema.org JobPosting when it
has one, or for a named block of the page when it does not. Most company and
agency sites in boards.json need nothing more than that entry.

The rest are small platforms or sites whose data comes another way (a JSON
blob in the page, an embed script, an Algolia index), one function each.

Same contract as ats_simple: a board's entry in, a Collected out.
Standard library only.
"""

from __future__ import annotations

import html
import json
import re
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from extract_common import (
    DETAIL_WORKERS,
    Collected,
    StealthBrowser,
    advert_from_page,
    html_to_text,
    inner_html,
    json_ld_job,
    json_ld_place,
    matches_query,
    page_main_text,
    pdf_text,
    render_page,
    request_json,
    request_text,
)

MAX_PAGES = 100


def _pool(function, items):
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        return list(pool.map(function, items))


def _clean(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _iso(value: Any) -> str:
    match = re.match(r"\d{4}-\d{2}-\d{2}", str(value or ""))
    return match.group(0) if match else ""


def _date_any(text: str) -> str:
    """An ISO date from the common ways a page writes one, or ""."""
    text = str(text or "").strip()
    if not text:
        return ""
    if _iso(text):
        return _iso(text)
    match = re.search(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", text)
    if match:
        return f"{match.group(3)}-{int(match.group(2)):02d}-{int(match.group(1)):02d}"
    for pattern in ("%d %B %Y", "%B %d, %Y", "%d %b %Y", "%b %d, %Y", "%d %B, %Y"):
        try:
            return datetime.strptime(re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text), pattern).date().isoformat()
        except ValueError:
            continue
    return ""


_TITLE_RE = re.compile(r"(?is)<h1\b[^>]*>(.*?)</h1>")
_OG_TITLE_RE = re.compile(r'<meta[^>]+property="og:title"[^>]+content="([^"]*)"')


# ----------------------------------------------------------------- html_list


def html_list(cfg: dict[str, Any]) -> Collected:
    """A list page and its posting pages, described in the board's entry.

    `list`: the list page URL (or a list of them).
    `link`: regex whose first group is a posting link on the list page; or,
        with named groups, (?P<url>…) and (?P<title>…) in either order, so the
        title can come from the list (an aria-label, the card's heading), and
        optionally (?P<location>…) when only the list says where the job is.
    `exclude`: regex for links that match `link` but are not postings.
    `next`: regex whose first group is the next list page's URL.
    `title_link`: true to take the title from the link text, not the page.
    `title_strip`: regex removed from titles ("^\\d+\\s+" for numbered cards).
    `desc_class`: class (one token) of the block holding the advert, when
        the posting page has no JobPosting; otherwise <main>/<article>.
    `location_class`, `date_class`, `deadline_class`: blocks holding those.
    `deadline_re`, `date_re`, `location_re`: regexes whose first group, found
        in the advert's text, is that fact ("Closing date: (.+?)\n").
    `count`: regex whose first group is the total the list page states.
    `empty`: regex that, on the list page, means "no openings right now".
    `query`: narrow to postings whose title or advert has every word.
    `detail`: false to stop at the list page (the link text is all there is).
    `pdf`: true when each posting is a PDF notice rather than a page; its
        text comes from pdftotext, its title from the list (`title` group),
        or from the notice's first lines when the list only says "Download".
    `pdf_link`: regex whose first group, on the posting page, is the PDF
        notice holding the advert the page itself leaves out.
    `render`: true when the list is built by the page's scripts: the page is
        loaded in headless Chrome first. `render_detail` likewise for postings.
    """
    lists = cfg["list"] if isinstance(cfg["list"], list) else [cfg["list"]]
    link_re = re.compile(cfg.get("link", r'href="([^"]*(?:job|vacanc|career|position|offer)[^"]*)"'), re.I | re.S)
    exclude_re = re.compile(cfg["exclude"], re.I) if cfg.get("exclude") else None
    next_re = re.compile(cfg["next"], re.I | re.S) if cfg.get("next") else None
    links: dict[str, str] = {}
    #: A location the list gives, from a (?P<location>…) group of `link`.
    list_places: dict[str, str] = {}
    stated = None
    empty = False
    seen_pages: set[str] = set()
    pages = list(lists)
    while pages and len(seen_pages) < MAX_PAGES:
        url = pages.pop(0)
        if url in seen_pages:
            continue
        seen_pages.add(url)
        page = render_page(url) if cfg.get("render") else request_text(url)
        if cfg.get("render") and not link_re.search(page):
            # Scripts sometimes finish after the first wait; once more, longer.
            page = render_page(url, wait_ms=40000)
        if cfg.get("count"):
            match = re.search(cfg["count"], _clean(page), re.I)
            stated = int(match.group(1)) if match else stated
        if cfg.get("empty") and re.search(cfg["empty"], _clean(page), re.I):
            empty = True
        for match in link_re.finditer(page):
            named = match.groupdict()
            href = html.unescape(named.get("url") or match.group(1)).strip()
            absolute = urllib.parse.urljoin(url, href).split("#")[0]
            if exclude_re and (exclude_re.search(absolute) or exclude_re.search(_clean(named.get("title") or ""))):
                continue
            if absolute in lists or absolute.rstrip("/") in (u.rstrip("/") for u in lists):
                continue
            if named.get("title") is not None:
                text = _clean(named["title"])
            else:
                text = _clean(match.group(2)) if match.lastindex and match.lastindex >= 2 else ""
            if text or absolute not in links:
                links[absolute] = text or links.get(absolute, "")
            if named.get("location"):
                list_places[absolute] = _clean(named["location"])
        if next_re:
            for match in next_re.finditer(page):
                pages.append(urllib.parse.urljoin(url, html.unescape(match.group(1))))

    def detail(item: tuple[str, str]) -> dict[str, Any]:
        url, link_text = item
        parts = urllib.parse.urlsplit(url)
        segments = [part for part in parts.path.split("/") if part]
        # The posting's own segment, not a trailing language code (/123/it/);
        # else a numeric query value (…/jobreqcareer?jobId=3032&company=…).
        numeric = [v for _, v in urllib.parse.parse_qsl(parts.query) if v.isdigit()]
        job_id = next(
            (part for part in reversed(segments) if re.search(r"\d", part)),
            numeric[0] if numeric else (segments[-1] if segments else url),
        )
        record: dict[str, Any] = {"job_id": job_id, "title": link_text, "url": url}
        if cfg.get("detail") is False:
            record["description"] = ""
            return record
        if cfg.get("pdf") or urllib.parse.urlsplit(url).path.lower().endswith(".pdf"):
            text = pdf_text(url)
            if not record["title"] or record["title"].lower() in ("download", "pdf", "here"):
                head = [line.strip() for line in text.splitlines() if line.strip()][:2]
                record["title"] = " ".join(head)[:140]
            return {**record, "description": text}
        try:
            page = render_page(url) if cfg.get("render_detail") else request_text(url)
        except RuntimeError as exc:
            print(f"  ! no detail for {url}: {exc}", file=sys.stderr)
            return {**record, "description": ""}
        posting = json_ld_job(page)
        city, country = json_ld_place(posting)
        title = html.unescape(str(posting.get("title") or "")).strip()
        if not title or cfg.get("title_link"):
            title = link_text or _clean(next(iter(_TITLE_RE.findall(page)), "")) or _clean(
                next(iter(_OG_TITLE_RE.findall(page)), "")
            )
        notice = re.search(cfg["pdf_link"], page) if cfg.get("pdf_link") else None
        if notice:
            advert = pdf_text(urllib.parse.urljoin(url, html.unescape(notice.group(1))))
        elif cfg.get("desc_class"):
            block = next(iter(inner_html(page, "class", cfg["desc_class"], token=True)), "")
            advert = html_to_text(block)
        elif posting.get("description"):
            advert = html_to_text(html.unescape(str(posting["description"])))
        else:
            advert = page_main_text(page)

        def block_text(key: str) -> str:
            if not cfg.get(key):
                return ""
            return _clean(next(iter(inner_html(page, "class", cfg[key], token=True)), ""))

        def from_text(key: str) -> str:
            match = re.search(cfg[key], advert, re.I) if cfg.get(key) else None
            return match.group(1).strip() if match else ""

        return {
            **record,
            "title": title,
            "location": block_text("location_class") or from_text("location_re") or city
            or list_places.get(url, ""),
            "country": country,
            "contract": str(posting.get("employmentType") or ""),
            "published": _iso(posting.get("datePosted"))
            or _date_any(block_text("date_class"))
            or _date_any(from_text("date_re")),
            "deadline": _iso(posting.get("validThrough"))
            or _date_any(block_text("deadline_class"))
            or _date_any(from_text("deadline_re")),
            "description": advert,
        }

    records = [record for record in _pool(detail, list(links.items())) if record.get("title")]
    if cfg.get("title_strip"):
        for record in records:
            record["title"] = re.sub(cfg["title_strip"], "", record["title"]).strip()
    notes = []
    if cfg.get("query"):
        before = len(records)
        records = [r for r in records if matches_query(cfg["query"], r["title"], r.get("description", ""))]
        notes.append(f'narrowed to "{cfg["query"]}": {len(records)} of {before}')
    total = len(records) if cfg.get("query") or stated is None else stated
    missing = sum(1 for r in records if not r.get("description")) if cfg.get("detail") is not False else 0
    return Collected(records, total, missing, notes, confirmed_empty=empty or bool(cfg.get("query") and links))


# ---------------------------------------------------------------------- Softy


def softy(cfg: dict[str, Any]) -> Collected:
    """`host`: <company>.softy.pro. The offers page is an Inertia app; its
    data-page JSON holds every offer, paginated, advert included."""
    host = cfg["host"]
    offers: list[dict[str, Any]] = []
    total = 0
    for number in range(1, MAX_PAGES + 1):
        page = request_text(f"https://{host}/offers?page={number}")
        data = json.loads(html.unescape(re.search(r'data-page="([^"]+)"', page).group(1)))
        block = data["props"]["offers"]
        total = int(block.get("total") or 0)
        offers.extend(block.get("data") or [])
        if number >= int(block.get("last_page") or 1):
            break
    def advert(offer: dict[str, Any]) -> dict[str, Any]:
        """The offer page's own data-page holds the advert the list leaves out."""
        try:
            page = request_text(f"https://{host}/offers/{offer.get('softy_id') or offer.get('id')}")
            full = json.loads(html.unescape(re.search(r'data-page="([^"]+)"', page).group(1)))["props"]["offer"]
        except (RuntimeError, AttributeError, KeyError, ValueError) as exc:
            print(f"  ! no detail for {offer.get('softy_id')}: {exc}", file=sys.stderr)
            full = {}
        return {**offer, **{k: v for k, v in full.items() if v}}

    offers = _pool(advert, offers)
    records = []
    for offer in offers:
        places = [
            ", ".join(part for part in (place.get("city"), place.get("country")) if part)
            for place in offer.get("locations") or []
        ]
        advert = "\n\n".join(
            html_to_text(offer.get(key) or "")
            for key in ("description_html", "searched_profile_html", "company_description_html")
            if offer.get(key)
        )
        records.append(
            {
                "job_id": str(offer.get("softy_id") or offer.get("id")),
                "title": str(offer.get("title") or "").strip(),
                "location": places[0] if places else "",
                "additional_locations": "; ".join(places[1:]),
                "contract": (offer.get("contract_type") or {}).get("name", ""),
                "department": offer.get("subsidiary_name") or "",
                "published": _iso(offer.get("recruitment_start") or offer.get("updated_at")),
                "url": f"https://{host}/offers/{offer.get('softy_id') or offer.get('id')}",
                "description": advert,
            }
        )
    return Collected(records, total, sum(1 for r in records if not r["description"]), confirmed_empty=True)


# --------------------------------------------------------------------- Comeet


def comeet(cfg: dict[str, Any]) -> Collected:
    """`uid` and `token`: the company's public careers-API pair, as the
    employer's own careers page embeds them (comeet.co/careers-api)."""
    positions = request_json(
        f"https://www.comeet.co/careers-api/2.0/company/{cfg['uid']}/positions"
        f"?token={cfg['token']}&details=true"
    )
    records = []
    for position in positions:
        location = position.get("location") or {}
        sections = [
            f"{item.get('name', '')}\n{html_to_text(item.get('value') or '')}".strip()
            for item in position.get("details") or []
            if item.get("value")
        ]
        records.append(
            {
                "job_id": position.get("uid", ""),
                "title": str(position.get("name") or "").strip(),
                "location": ", ".join(part for part in (location.get("city"), location.get("country")) if part)
                or location.get("name", ""),
                "department": position.get("department") or "",
                "contract": position.get("employment_type") or "",
                "published": _iso(position.get("time_updated")),
                "url": position.get("url_active_page") or position.get("url_comeet_hosted_page") or "",
                "description": "\n\n".join(sections),
            }
        )
    return Collected(records, len(records), sum(1 for r in records if not r["description"]), confirmed_empty=True)


# ---------------------------------------------------------------------- HiBob


def hibob(cfg: dict[str, Any]) -> Collected:
    """`subdomain`: <subdomain>.careers.hibob.com. Its careers app reads
    /api/job-ad, which answers when told the company by header."""
    company = cfg["subdomain"]
    payload = request_json(
        f"https://{company}.careers.hibob.com/api/job-ad", headers={"companyidentifier": company}
    )
    records = []
    for ad in payload.get("jobAdDetails") or []:
        advert = "\n\n".join(
            html_to_text(ad.get(key) or "") for key in ("description", "requirements", "responsibilities", "benefits") if ad.get(key)
        )
        records.append(
            {
                "job_id": ad.get("id", ""),
                "title": str(ad.get("title") or "").strip(),
                "location": ad.get("site") or "",
                "country": ad.get("country") or "",
                "department": ad.get("department") or "",
                "contract": ad.get("employmentType") or "",
                "url": f"https://{company}.careers.hibob.com/jobs/{ad.get('id')}",
                "description": advert,
            }
        )
    return Collected(records, len(records), sum(1 for r in records if not r["description"]), confirmed_empty=True)


# --------------------------------------------------------------------- Taleez


def taleez(cfg: dict[str, Any]) -> Collected:
    """`host`: <company>.taleez.com. The site's app reads /api/careez, which
    lists the jobs; each advert is on its public taleez.com/apply page."""
    from datetime import timezone

    site = request_json(f"https://{cfg['host']}/api/careez")
    jobs = site.get("jobs") or []

    def detail(job: dict[str, Any]) -> dict[str, Any]:
        url = f"https://taleez.com/apply/{job.get('slug')}"
        try:
            advert = page_main_text(request_text(url))
        except RuntimeError as exc:
            print(f"  ! no detail for {url}: {exc}", file=sys.stderr)
            advert = ""
        location = job.get("location") or {}
        published = job.get("publishDate") or job.get("creationDate")
        return {
            "job_id": str(job.get("id", "")),
            "title": str(job.get("label") or "").strip(),
            "location": location.get("city") or location.get("label") or location.get("postalCode") or "",
            "contract": job.get("contract") or "",
            "remote_type": "Remote" if job.get("remote") else "",
            "published": datetime.fromtimestamp(int(published) / 1000, timezone.utc).date().isoformat()
            if published
            else "",
            "url": url,
            "description": advert,
        }

    records = _pool(detail, jobs)
    return Collected(records, len(jobs), sum(1 for r in records if not r["description"]), confirmed_empty=True)


# ------------------------------------------------------------------------ DNV


def dnv(cfg: dict[str, Any]) -> Collected:
    """jobs.dnv.com lists its postings from Algolia (public search key in the
    page) and they live in Oracle Recruiting Cloud; `business_unit` filters
    the index as the site's own filter does, adverts come from Oracle."""
    from ats_enterprise import _oracle_advert, _oracle_detail, algolia_search

    page = request_text("https://jobs.dnv.com/job-search")
    app_id = re.search(r'AG_ID\s*=\s*"([^"]+)"', page).group(1)
    api_key = re.search(r'AG_KEY\s*=\s*"([^"]+)"', page).group(1)
    index = json.loads(re.search(r"AG_INDEX\s*=\s*(\{.*?\});", page).group(1))["jobs"]
    filters = [[f"business_unit:{cfg['business_unit']}"]] if cfg.get("business_unit") else []
    hits, total = algolia_search(app_id, api_key, index, cfg.get("query", ""), filters)

    def build(hit: dict[str, Any]) -> dict[str, Any]:
        match = re.search(r"/job/(\d+)", hit.get("apply_url") or "")
        detail = _oracle_detail("ecyq.fa.em2.oraclecloud.com", "CX_1", match.group(1)) if match else {}
        return {
            "job_id": match.group(1) if match else hit.get("objectID", ""),
            "title": str(hit.get("title") or "").strip(),
            "location": hit.get("display_location") or "",
            "department": hit.get("business_unit") or "",
            "contract": hit.get("contract_type") or "",
            "published": _iso(detail.get("ExternalPostedStartDate")),
            "deadline": _iso(detail.get("ExternalPostedEndDate")),
            "url": urllib.parse.urljoin("https://jobs.dnv.com", hit.get("jd_url") or ""),
            "description": _oracle_advert(detail),
        }

    records = _pool(build, hits)
    return Collected(records, total, sum(1 for r in records if not r["description"]), confirmed_empty=True)


# --------------------------------------------------------------------- Onlyfy

_ONLYFY_CARD_RE = re.compile(r'aria-label="([^"]+)" href="(/[a-z]{2}/job/[a-z0-9]+)"')
_NEXT_PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)')


def onlyfy(cfg: dict[str, Any]) -> Collected:
    """`host`: <company>.onlyfy.jobs; optional `lang` (default en).

    The career page lists every job as a card (title in aria-label). A job
    page is a Next.js app whose advert travels as one of its streamed text
    chunks: plain text, where the framework's own chunks start "<n>:[".
    """
    host, lang = cfg["host"], cfg.get("lang", "en")
    page = request_text(f"https://{host}/{lang}")
    cards = dict((path, html.unescape(title)) for title, path in _ONLYFY_CARD_RE.findall(page))

    def detail(item: tuple[str, str]) -> dict[str, Any]:
        path, title = item
        url = f"https://{host}{path}"
        try:
            job_page = request_text(url)
        except RuntimeError as exc:
            print(f"  ! no detail for {url}: {exc}", file=sys.stderr)
            job_page = ""
        texts = []
        for chunk in _NEXT_PUSH_RE.findall(job_page):
            try:
                text = json.loads(f'"{chunk}"')
            except ValueError:
                continue
            if not re.match(r"^[0-9a-f]+:", text):
                texts.append(text)
        advert = max(texts, key=len, default="")
        advert = html_to_text(advert) if "<" in advert else advert.strip()
        if not advert:
            # Some pages load the advert after the fact (G DATA); the job's
            # print view, a PDF, always holds it.
            job_id = path.rsplit("/", 1)[-1]
            advert = pdf_text(f"https://{host}/candidate/job/print/{job_id}?mode=print&lang={lang}")
        return {
            "job_id": path.rsplit("/", 1)[-1],
            "title": title.strip(),
            "url": url,
            "description": advert,
        }

    records = _pool(detail, list(cards.items()))
    return Collected(records, len(cards), sum(1 for r in records if not r["description"]), confirmed_empty=True)


# ------------------------------------------------------------ ADP WorkforceNow


def adp(cfg: dict[str, Any]) -> Collected:
    """`cid` (and optional `ccId`, `lang`): the career center's ids, as in its
    recruitment.html link. ADP's career center reads a public staffing API."""
    base = "https://workforcenow.adp.com/mascsr/default/careercenter/public/events/staffing/v1/job-requisitions"
    query = urllib.parse.urlencode(
        {"cid": cfg["cid"], "ccId": cfg.get("ccId", "19000101_000001"), "lang": cfg.get("lang", "en_US"), "locale": cfg.get("lang", "en_US")}
    )
    requisitions: list[dict[str, Any]] = []
    total = 0
    for _ in range(MAX_PAGES):
        payload = request_json(f"{base}?{query}&$top=50&$skip={len(requisitions)}")
        total = int((payload.get("meta") or {}).get("totalNumber") or 0)
        page = payload.get("jobRequisitions") or []
        requisitions.extend(page)
        if not page or len(requisitions) >= total:
            break

    def build(requisition: dict[str, Any]) -> dict[str, Any]:
        rid = requisition.get("itemID")
        try:
            full = request_json(f"{base}/{rid}?{query}")
        except RuntimeError as exc:
            print(f"  ! no detail for {rid}: {exc}", file=sys.stderr)
            full = {}
        places = []
        for place in requisition.get("requisitionLocations") or []:
            address = place.get("address") or {}
            places.append(", ".join(str(part) for part in (address.get("cityName"), (address.get("countrySubdivisionLevel1") or {}).get("codeValue"), address.get("countryCode")) if part))
        return {
            "job_id": requisition.get("clientRequisitionID") or rid,
            "title": str(requisition.get("requisitionTitle") or "").strip(),
            "location": places[0] if places else "",
            "additional_locations": "; ".join(places[1:]),
            "published": _iso(requisition.get("postDate")),
            "url": f"https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid={cfg['cid']}&ccId={cfg.get('ccId', '19000101_000001')}&jobId={rid}&lang={cfg.get('lang', 'en_US')}",
            "description": html_to_text(full.get("requisitionDescription") or ""),
        }

    records = _pool(build, requisitions)
    return Collected(records, total, sum(1 for r in records if not r["description"]), confirmed_empty=True)


# ------------------------------------------------------------------ html_inline


_FLIGHT_TEXT_RE = re.compile(rb"([0-9a-f]+):T([0-9a-f]+),")
_FLIGHT_JOB_RE = re.compile(r'"title":("(?:[^"\\]|\\.)*"),"description":"\$([0-9a-f]+)"')


def _flight_adverts(page: str) -> dict[str, str]:
    """Advert HTML by title, from a Next.js page's streamed data.

    The page shows each posting as a collapsed card; the advert it opens to
    travels in the stream as a text row ("1d:T232d,<that many bytes>") that
    the posting's "description":"$1d" points to.
    """
    stream = "".join(json.loads(f'"{chunk}"') for chunk in _NEXT_PUSH_RE.findall(page)).encode()
    texts = {
        match.group(1).decode(): stream[match.end() : match.end() + int(match.group(2), 16)].decode("utf-8", "replace")
        for match in _FLIGHT_TEXT_RE.finditer(stream)
    }
    return {
        _clean(json.loads(title)): texts[ref]
        for title, ref in _FLIGHT_JOB_RE.findall(stream.decode("utf-8", "replace"))
        if ref in texts
    }


def html_inline(cfg: dict[str, Any]) -> Collected:
    """Postings written out on one page, with no page of their own.

    `list`: the page (`render` to load it in a browser first). `item`: regex
    with named groups (?P<title>…) and, optionally, (?P<body>…) matching one
    posting each; the posting's URL is the page with an anchor made from its
    title, so each keeps an identity of its own between runs. `empty`: regex
    meaning "no openings right now". `flight`: true when the cards open to
    an advert held in the page's Next.js data rather than in its HTML.
    """
    page = render_page(cfg["list"]) if cfg.get("render") else request_text(cfg["list"])
    adverts = _flight_adverts(page) if cfg.get("flight") else {}
    records = []
    seen: set[str] = set()
    for match in re.finditer(cfg["item"], page, re.S | re.I):
        title = _clean(match.group("title"))
        if not title or title in seen:
            continue
        seen.add(title)
        body = match.groupdict().get("body") or ""
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        records.append(
            {
                "job_id": slug,
                "title": title,
                "url": f"{cfg['list'].split('#')[0]}#{slug}",
                "description": "\n\n".join(text for text in (html_to_text(body), html_to_text(adverts.get(title, ""))) if text),
            }
        )
    empty = bool(cfg.get("empty") and re.search(cfg["empty"], _clean(page), re.I))
    return Collected(records, len(records), 0, confirmed_empty=empty)


# ------------------------------------------------------------- UN careers


def un_careers(cfg: dict[str, Any]) -> Collected:
    """careers.un.org: its app posts a filter to a public list endpoint, and
    each opening carries its advert. `query` is the keyword the link carries."""
    url = "https://careers.un.org/api/public/opening/jo/list/filteredV2/en"
    openings: list[dict[str, Any]] = []
    total = 0
    for number in range(MAX_PAGES):
        payload = request_json(
            url,
            {
                "filterConfig": {"keyword": cfg.get("query", "")},
                "pagination": {"page": number, "itemPerPage": 50, "sortBy": "startDate", "sortDirection": -1},
            },
        )
        data = payload.get("data") or {}
        total = int(data.get("count") or 0)
        page = data.get("list") or []
        openings.extend(page)
        if not page or len(openings) >= total:
            break
    records = []
    for opening in openings:
        stations = [station.get("description", "").title() for station in opening.get("dutyStation") or []]
        records.append(
            {
                "job_id": str(opening.get("jobId", "")),
                "title": str(opening.get("postingTitle") or opening.get("jobTitle") or "").strip(),
                "location": stations[0] if stations else "",
                "additional_locations": "; ".join(stations[1:]),
                "department": (opening.get("dept") or {}).get("name", ""),
                "contract": (opening.get("jc") or {}).get("name", ""),
                "grade": opening.get("jobLevel", ""),
                "published": _iso(opening.get("startDate")),
                "deadline": _iso(opening.get("endDate")),
                "url": f"https://careers.un.org/jobSearchDescription/{opening.get('jobId')}?language=en",
                "description": html_to_text(opening.get("jobDescription") or ""),
            }
        )
    return Collected(records, total, sum(1 for r in records if not r["description"]), confirmed_empty=True)


# ------------------------------------------------------------- Telekom


_TELEKOM_API = "https://careers.telekom.com/api/jobs-proxy"


def telekom(cfg: dict[str, Any]) -> Collected:
    """careers.telekom.com: `query` is the search the link carries.

    The page is behind a bot check, but its own app reads a public JSON
    proxy. The search is a POST (`/search`, the site's own semantic search,
    which returns what the page shows) whose results carry only titles; the
    full records, advert included, are in the paged list of every opening
    (`/jobs`), where the searched ones are picked out by requisition id.
    """
    found = request_json(f"{_TELEKOM_API}/search", {"user_query": cfg["query"], "locale": "en"})
    wanted = {str(job.get("requisition_id")): job for job in found.get("data") or []}
    full: dict[str, dict[str, Any]] = {}
    for number in range(1, MAX_PAGES + 1):
        page = request_json(f"{_TELEKOM_API}/jobs?per_page=100&page={number}")
        for job in page.get("data") or []:
            if str(job.get("requisition_id")) in wanted:
                full[str(job["requisition_id"])] = job
        pages = int((page.get("pagination") or {}).get("total_pages") or 0)
        if len(full) == len(wanted) or number >= pages:
            break

    def known(value: Any) -> str:
        text = str(value or "").strip()
        return "" if text in ("None", "null") else text

    records = []
    for job_id, short in wanted.items():
        job = {**short, **full.get(job_id, {})}
        advert = "\n\n".join(
            html_to_text(job.get(key) or "")
            for key in ("job_desc_markdown", "requirement_description_md", "company_description_md")
            if job.get(key)
        )
        records.append(
            {
                "job_id": job_id,
                "title": str(job.get("job_title") or job.get("title") or "").strip(),
                "location": known(job.get("location")) or known(job.get("city")),
                "country": known(job.get("country")),
                "department": known(job.get("category")),
                "experience": known(job.get("experience_level")),
                "contract": known(job.get("job_type")),
                "employer": known(job.get("company")),
                "published": _iso(job.get("creation_datetime")),
                "deadline": _iso(known(job.get("application_deadline"))),
                "url": str(job.get("apply_url") or "").strip(),
                "description": advert,
            }
        )
    missing = sum(1 for record in records if not record["description"])
    return Collected(records, len(wanted), missing, confirmed_empty=True)


# ---------------------------------------------------------- Jane Street

_JANE_STREET = "https://www.janestreet.com"
#: The site's own city codes, as its open-roles page names them.
_JANE_STREET_CITIES = {
    "NYC": "New York, United States", "LDN": "London, United Kingdom", "HKG": "Hong Kong",
    "AMS": "Amsterdam, Netherlands", "CHI": "Chicago, United States", "SGP": "Singapore",
    "MUM": "Mumbai, India", "SHA": "Shanghai, China", "PHL": "Philadelphia, United States",
    "SF": "San Francisco, United States", "ATX": "Austin, United States",
}


def janestreet(cfg: dict[str, Any]) -> Collected:
    """janestreet.com open roles. `type`: "experienced-candidates" or
    "students-and-new-grads", as the link's ?type=; `city`: the site's code
    for ?location= (LDN for london). Optional.

    The page filters one JSON file of every role in the browser; this reads
    the same file and applies the same filters: experienced is "Full-Time:
    Experienced", students and new grads is every other kind (new grad,
    internships). Only roles in position-directories.json are shown there.
    """
    roles = request_json(f"{_JANE_STREET}/jobs/main.json")
    listed = {str(item) for item in request_json(f"{_JANE_STREET}/static/position-directories.json")}
    kind, city = cfg.get("type", ""), cfg.get("city", "")
    records = []
    for role in roles:
        experienced = role.get("availability") == "Full-Time: Experienced"
        if str(role.get("id")) not in listed:
            continue
        if kind == "experienced-candidates" and not experienced:
            continue
        if kind == "students-and-new-grads" and experienced:
            continue
        if city and role.get("city") != city:
            continue
        low, high = role.get("min_salary"), role.get("max_salary")
        records.append(
            {
                "job_id": str(role["id"]),
                "title": str(role.get("position") or "").strip(),
                "location": _JANE_STREET_CITIES.get(role.get("city") or "", role.get("city") or ""),
                "department": role.get("category") or "",
                "team": role.get("team") or "",
                "contract": role.get("availability") or "",
                "duration": role.get("duration") or "",
                "salary": f"{low}-{high}" if low and high else "",
                "url": f"{_JANE_STREET}/join-jane-street/position/{role['id']}/",
                "description": html_to_text(role.get("overview") or ""),
            }
        )
    return Collected(records, len(records), sum(1 for r in records if not r["description"]), confirmed_empty=True)


# --------------------------------------------------------------- Stripe

_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)


def stripe(cfg: dict[str, Any]) -> Collected:
    """stripe.com/careers/search: `query` and `region` (a location the page
    groups others under, "Europe") as the link carries them.

    The page holds every listing in its Next.js data and filters in the
    browser: the query against the title and the team, the region against
    each listing's places and their parents. Read the same way, the result
    is the one the page shows. The adverts come from Stripe's Greenhouse
    board, which the listings name by id.
    """
    page = request_text(cfg["url"])
    index = json.loads(_NEXT_DATA_RE.search(page).group(1))["props"]["pageProps"]["jobIndexData"]
    places, teams = index["filters"]["locations"], index["filters"]["teams"]
    region = next((i for i, place in enumerate(places) if place["name"] == cfg.get("region")), None)

    def within(i: int | None) -> bool:
        while i is not None:
            if i == region:
                return True
            i = places[i].get("parentLocationIndex")
        return False

    query = cfg.get("query", "").lower()
    listings = [
        job
        for job in index["listings"]
        if (region is None or any(within(i) for i in job["locationIndices"]))
        and (
            not query
            or query in job["title"].lower()
            or any(query in teams[i]["name"].lower() for i in job["teamIndices"])
        )
    ]
    board = request_json("https://boards-api.greenhouse.io/v1/boards/stripe/jobs?content=true")
    adverts = {job["id"]: html_to_text(html.unescape(job.get("content") or "")) for job in board.get("jobs") or []}
    records = [
        {
            "job_id": str(job["greenhouseId"]),
            "title": job["title"].strip(),
            "location": "; ".join(
                places[i]["name"] for i in job["locationIndices"] if region is None or within(i)
            ),
            "department": ", ".join(teams[i]["name"] for i in job["teamIndices"]),
            "contract": job.get("employmentType") or "",
            "url": f"https://stripe.com/careers/listing/{job['slug']}/{job['greenhouseId']}",
            "description": adverts.get(job["greenhouseId"], ""),
        }
        for job in listings
    ]
    missing = sum(1 for record in records if not record["description"])
    return Collected(records, len(records), missing, confirmed_empty=True)


# -------------------------------------------------------------- Booking


def booking(cfg: dict[str, Any]) -> Collected:
    """jobs.booking.com: `query` is the link's keywords. The search page
    reads a public JSON endpoint, advert included."""
    found: dict[str, dict[str, Any]] = {}
    total = 0
    for number in range(1, MAX_PAGES + 1):
        page = request_json(
            "https://jobs.booking.com/api/jobs?"
            + urllib.parse.urlencode({"keywords": cfg["query"], "page": number, "limit": 100})
        )
        total = int(page.get("totalCount") or 0)
        batch = [job.get("data") or {} for job in page.get("jobs") or []]
        for job in batch:
            found.setdefault(str(job.get("req_id") or job.get("slug")), job)
        if not batch or len(found) >= total:
            break
    records = [
        {
            "job_id": job_id,
            "title": str(job.get("title") or "").strip(),
            "location": ", ".join(part for part in (job.get("city"), job.get("country")) if part),
            "department": ", ".join(c.get("name", "") for c in job.get("categories") or []),
            "contract": job.get("employment_type") or "",
            "published": _iso(job.get("posted_date")),
            "url": f"https://jobs.booking.com/booking/jobs/{job.get('slug') or job_id}",
            "description": html_to_text(job.get("description") or ""),
        }
        for job_id, job in found.items()
    ]
    return Collected(records, total, sum(1 for r in records if not r["description"]), confirmed_empty=True)


# -------------------------------------------------------------- Spotify


def spotify(cfg: dict[str, Any]) -> Collected:
    """lifeatspotify.com/jobs: `params`, the link's query string ("l=stockholm").

    The page reads a public search endpoint; the advert is on each job's own
    page, which the end of the run opens (the listing carries none).
    """
    payload = request_json(f"https://api.lifeatspotify.com/wp-json/animal/v1/job/search?{cfg.get('params', '')}")
    records = []
    for job in payload.get("result") or []:
        categories = [c.get("name", "") for c in (job.get("main_category"), job.get("sub_category")) if c]
        records.append(
            {
                "job_id": job["id"],
                "title": str(job.get("text") or "").strip(),
                "location": "; ".join(place.get("location", "") for place in job.get("locations") or []),
                "department": " / ".join(categories),
                "contract": (job.get("job_type") or {}).get("name", ""),
                "url": f"https://www.lifeatspotify.com/jobs/{job['id']}",
                "description": "",
            }
        )
    return Collected(records, len(records), 0, confirmed_empty=True)


# -------------------------------------------------------------- Datadog


def datadog(cfg: dict[str, Any]) -> Collected:
    """careers.datadoghq.com: `query`, the link's ?s=. The site searches a
    Typesense collection with a public search-only key it ships in its own
    script; the English postings are what the page lists, advert included."""
    host, key = "gk6e3zbyuntvc5dap.a1.typesense.net", "1Hwq7hntXp211hKvRS3CSI2QSU7w2gFm"
    hits: list[dict[str, Any]] = []
    total = 0
    for number in range(1, MAX_PAGES + 1):
        page = request_json(
            f"https://{host}/collections/careers_alias/documents/search?"
            + urllib.parse.urlencode(
                {"q": cfg["query"], "preset": "careers_list_view", "filter_by": "language:=en",
                 "per_page": 250, "page": number}
            ),
            headers={"X-TYPESENSE-API-KEY": key},
        )
        total = int(page.get("found") or 0)
        batch = [hit["document"] for hit in page.get("hits") or []]
        hits.extend(batch)
        if not batch or len(hits) >= total:
            break
    records = [
        {
            "job_id": str(job.get("job_id") or job.get("id")),
            "title": str(job.get("title") or "").strip(),
            "location": job.get("location_string") or "",
            "department": job.get("department") or "",
            "team": job.get("team") or "",
            "url": job.get("absolute_url") or f"https://careers.datadoghq.com/detail/{job.get('job_id')}/",
            "description": html_to_text(html.unescape(job.get("description") or "")),
        }
        for job in hits
    ]
    return Collected(records, total, sum(1 for r in records if not r["description"]), confirmed_empty=True)


# ----------------------------------------------------------------- Deel


def _flight_stream(page: str) -> tuple[str, dict[str, str]]:
    """A Next.js page's streamed data, and its text rows by id ("1a:T…,")."""
    raw = "".join(json.loads(f'"{chunk}"') for chunk in _NEXT_PUSH_RE.findall(page)).encode()
    texts = {
        match.group(1).decode(): raw[match.end() : match.end() + int(match.group(2), 16)].decode("utf-8", "replace")
        for match in _FLIGHT_TEXT_RE.finditer(raw)
    }
    return raw.decode("utf-8", "replace"), texts


def deel(cfg: dict[str, Any]) -> Collected:
    """jobs.deel.com/<board>: the page carries every posting in its Next.js
    data; each advert is a text row its posting points to ("$1a")."""
    board = cfg["board"]
    stream, texts = _flight_stream(request_text(f"https://jobs.deel.com/{board}"))
    start = stream.find('"jobPostings":')
    postings, _ = json.JSONDecoder().raw_decode(stream, start + len('"jobPostings":'))
    records = []
    for posting in postings:
        job = posting.get("job") or {}
        advert = str(posting.get("richtextDescription") or "")
        if advert.startswith("$") and advert[1:] in texts:
            advert = texts[advert[1:]]
        places = [(item.get("location") or {}).get("name", "") for item in job.get("jobLocations") or []]
        records.append(
            {
                "job_id": posting["id"],
                "title": str(posting.get("title") or "").strip(),
                "location": "; ".join(place for place in places if place),
                "department": ", ".join(
                    (item.get("department") or {}).get("name", "") for item in job.get("jobDepartments") or []
                ),
                "contract": ", ".join(
                    (item.get("employmentType") or {}).get("name", "") for item in job.get("jobEmploymentTypes") or []
                ),
                "remote_type": job.get("workArrangementEnum") or "",
                "published": _iso(posting.get("createdAt")),
                "url": f"https://jobs.deel.com/{board}/job-details/{posting['id']}/overview",
                "description": html_to_text(advert) if "<" in advert else advert.strip(),
            }
        )
    return Collected(records, len(records), sum(1 for r in records if not r["description"]), confirmed_empty=True)


# -------------------------------------------------------------- Revolut


def _kebab(title: str) -> str:
    """Revolut's slug for a title: "Information Security Engineer (AppSec)"
    becomes "information-security-engineer-app-sec"."""
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", title)
    return re.sub(r"[^a-z0-9]+", "-", spaced.lower()).strip("-")


def revolut(cfg: dict[str, Any]) -> Collected:
    """revolut.com/careers: `query`, the link's ?text=.

    Behind Cloudflare: read with StealthBrowser (extract_common), as the
    operator asked. The careers page holds every opening in its Next.js data;
    the query is matched against titles, as the site's search does. The
    adverts are on each position's page (schema.org JobPosting).
    """
    query = cfg.get("query", "").lower()
    base = "https://www.revolut.com"
    records = []
    with StealthBrowser() as browser:
        listing = browser.get(f"{base}/careers/?text={urllib.parse.quote(query)}", ready=r'"positions":\[')
        data = json.loads(_NEXT_DATA_RE.search(listing).group(1))["props"]["pageProps"]
        positions = [p for p in data.get("positions") or [] if query in str(p.get("text") or "").lower()]
        # The page links each one; for those beyond its first batch the link
        # is built the way the site builds it.
        linked = {
            match.group(2): match.group(1)
            for match in re.finditer(r'href="(/careers/position/[^"]*?-([0-9a-f-]{36})/)"', listing)
        }
        for position in positions:
            path = linked.get(position["id"]) or f"/careers/position/{_kebab(position['text'])}-{position['id']}/"
            try:
                advert = advert_from_page(browser.get(base + path, ready=r"application/ld\+json"))
            except RuntimeError as exc:
                print(f"  ! no advert for {path}: {exc}", file=sys.stderr)
                advert = ""
            places = position.get("locations") or []
            records.append(
                {
                    "job_id": position["id"],
                    "title": str(position.get("text") or "").strip(),
                    "location": "; ".join(
                        f"{place.get('name')} ({place.get('type')})" for place in places if place.get("name")
                    ),
                    "country": "; ".join(dict.fromkeys(p.get("country", "") for p in places if p.get("country"))),
                    "department": position.get("team") or "",
                    "url": base + path,
                    "description": advert,
                }
            )
    return Collected(records, len(records), sum(1 for r in records if not r["description"]), confirmed_empty=True)


PLATFORMS = {
    "revolut": revolut,
    "booking": booking,
    "spotify": spotify,
    "datadog": datadog,
    "deel": deel,
    "stripe": stripe,
    "janestreet": janestreet,
    "telekom": telekom,
    "un_careers": un_careers,
    "adp": adp,
    "html_inline": html_inline,
    "dnv": dnv,
    "onlyfy": onlyfy,
    "html_list": html_list,
    "softy": softy,
    "comeet": comeet,
    "hibob": hibob,
    "taleez": taleez,
}

if __name__ == "__main__":  # python3 sites_custom.py html_list '{"list": …}'
    result = PLATFORMS[sys.argv[1]](json.loads(sys.argv[2]))
    print(result.total, len(result.records), result.missing_detail, result.notes, "confirmed_empty" if result.confirmed_empty else "")
    for record in result.records[:3]:
        print({k: str(v)[:70] for k, v in record.items()})

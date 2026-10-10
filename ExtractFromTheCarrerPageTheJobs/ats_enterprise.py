"""Job boards on the large enterprise platforms.

Eightfold, Cornerstone (CSOD), Oracle Recruiting Cloud, Avature, the classic
SuccessFactors career site, Talentsoft, Lumesse TalentLink and Radancy. Each needs more
than one request per page — a token, a site number, a session — and each
documents how on its function. Same contract as ats_simple: a board's entry
in, a Collected out.

Standard library only.
"""

from __future__ import annotations

import html
import json
import re
import sys
import urllib.parse
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
    request_json,
    request_text,
)

#: Safety stop for a listing that never runs out.
MAX_PAGES = 400


def _pool(function, items):
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        return list(pool.map(function, items))


def _iso(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)) or str(value).isdigit():
        number = int(value)
        # Seconds or milliseconds since the epoch, whichever it is.
        number = number / 1000 if number > 10**11 else number
        return datetime.fromtimestamp(number, timezone.utc).date().isoformat()
    match = re.match(r"\d{4}-\d{2}-\d{2}", str(value))
    return match.group(0) if match else ""


def _clean(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


# ------------------------------------------------------------------- Eightfold


def eightfold(cfg: dict[str, Any]) -> Collected:
    """`host`, `domain`; optional `query`, `filters` ({"filter_seniority": ["Entry", "Intern"]}).

    Two generations of the same API: /api/pcsx/search (Microsoft, Lockheed,
    Northrop, Infineon) and /api/apply/v2/jobs (STMicroelectronics); `api`
    says which, default pcsx. A filter with several values is sent as the
    parameter repeated — a comma-joined value matches nothing.
    """
    host, domain = cfg["host"], cfg["domain"]
    pcsx = cfg.get("api", "pcsx") == "pcsx"
    params = [("domain", domain), ("query", cfg.get("query", "")), ("sort_by", "relevance")]
    for key, values in (cfg.get("filters") or {}).items():
        params.extend((key, value) for value in (values if isinstance(values, list) else [values]))
    path = "/api/pcsx/search" if pcsx else "/api/apply/v2/jobs"

    positions: list[dict[str, Any]] = []
    seen: set[str] = set()
    total = 0
    for _ in range(MAX_PAGES):
        query = urllib.parse.urlencode(params + [("start", len(positions)), ("num", 50), ("location", "")])
        payload = request_json(f"https://{host}{path}?{query}")
        data = payload.get("data", payload) if pcsx else payload
        total = int(data.get("count") or total)
        page = [p for p in data.get("positions") or [] if str(p.get("id")) not in seen]
        seen.update(str(p.get("id")) for p in page)
        positions.extend(page)
        if not page or len(positions) >= total:
            break

    def detail(position: dict[str, Any]) -> dict[str, Any]:
        pid = position.get("id")
        url = (
            f"https://{host}/api/pcsx/position_details?position_id={pid}&domain={domain}&hl=en"
            if pcsx
            else f"https://{host}/api/apply/v2/jobs/{pid}?domain={domain}"
        )
        try:
            full = request_json(url)
            full = full.get("data", full) if pcsx else full
        except RuntimeError as exc:
            print(f"  ! no detail for {pid}: {exc}", file=sys.stderr)
            full = {}
        locations = position.get("locations") or position.get("standardizedLocations") or []
        if isinstance(locations, str):
            locations = [locations]
        public = full.get("publicUrl") or full.get("canonicalPositionUrl") or ""
        return {
            "job_id": str(position.get("displayJobId") or position.get("display_job_id") or pid),
            "title": str(position.get("name") or position.get("posting_name") or "").strip(),
            "location": locations[0] if locations else str(position.get("location") or ""),
            "additional_locations": "; ".join(locations[1:4]) + (" …" if len(locations) > 4 else ""),
            "department": position.get("department") or "",
            "remote_type": position.get("workLocationOption") or position.get("work_location_option") or "",
            "published": _iso(position.get("postedTs") or position.get("t_create")),
            "url": public or f"https://{host}/careers/job/{pid}",
            "description": html_to_text(full.get("jobDescription") or full.get("job_description") or ""),
        }

    records = _pool(detail, positions)
    return Collected(
        records, total, sum(1 for r in records if not r["description"]), confirmed_empty=True
    )


# ----------------------------------------------------------- Cornerstone (CSOD)

_CSOD_CONTEXT_RE = re.compile(r"csod\.context=(\{.*?\});", re.S)
#: CSS rules some employers paste into the advert as text (GMV's start with a
#: spinner's stylesheet): `.x { … }`, `@keyframes y { 0% { … } … }`.
_CSS_RULE_RE = re.compile(r"(?:@keyframes\s+[\w-]+\s*\{(?:[^{}]*\{[^{}]*\})*\s*\}|[.#][\w:-][^{}\n]{0,160}\{[^{}]*\})")


def _without_css(text: str) -> str:
    return re.sub(r"[ \t]{2,}", " ", _CSS_RULE_RE.sub(" ", text)).strip()


def csod(cfg: dict[str, Any]) -> Collected:
    """`corp` (<corp>.csod.com), `site` (careersite number); optional `query`, `lang`, `dropdowns`.

    The career site page carries a short-lived bearer token and the regional
    API host in `csod.context`; the search is a POST to that host, and each
    result already holds the full advert. `dropdowns` are the link's custom
    field filters: cfdd[0][id]=16&cfdd[0][options][0]=29 is
    [{"id": 16, "options": [29]}].
    """
    corp, site = cfg["corp"], int(cfg.get("site", 1))
    lang = cfg.get("lang", "en-US")
    page = request_text(f"https://{corp}.csod.com/ux/ats/careersite/{site}/home?c={corp}&lang={lang}")
    context = json.loads(_CSOD_CONTEXT_RE.search(page).group(1))
    endpoint = context["endpoints"]["cloud"] + "rec-job-search/external/jobs"
    headers = {"Authorization": f"Bearer {context['token']}"}

    def search(number: int) -> dict[str, Any]:
        body = {
            "careerSiteId": site,
            "careerSitePageId": site,
            "pageNumber": number,
            "pageSize": 100,
            "cultureId": context.get("cultureID", 1),
            "searchText": cfg.get("query", ""),
            "cultureName": lang,
            "states": [],
            "countryCodes": [],
            "cities": [],
            "placeID": "",
            "radius": None,
            "postingsWithinDays": None,
            "customFieldCheckboxKeys": [],
            "customFieldDropdowns": cfg.get("dropdowns", []),
            "customFieldRadios": [],
        }
        return request_json(endpoint, body, headers=headers).get("data") or {}

    found: dict[str, dict[str, Any]] = {}
    totals = []
    for number in range(1, MAX_PAGES + 1):
        data = search(number)
        totals.append(int(data.get("totalCount") or 0))
        requisitions = data.get("requisitions") or []
        for requisition in requisitions:
            found.setdefault(str(requisition.get("requisitionId")), requisition)
        if not requisitions or len(found) >= totals[0]:
            break
    # The same search has been seen to report different totals a moment
    # apart; the larger is the one to be held to.
    total = max(totals)

    records = []
    for rid, requisition in found.items():
        places = requisition.get("locations") or []
        named = [
            ", ".join(part for part in (place.get("city"), place.get("country")) if part)
            for place in places
            if isinstance(place, dict)
        ]
        posted = requisition.get("postingEffectiveDate") or ""
        try:
            posted = datetime.strptime(posted, "%m/%d/%Y").date().isoformat()
        except ValueError:
            posted = ""
        expires = requisition.get("postingExpirationDate") or ""
        try:
            expires = datetime.strptime(expires, "%m/%d/%Y").date().isoformat()
        except ValueError:
            expires = ""
        records.append(
            {
                "job_id": rid,
                # Cornerstone sends title and advert HTML-escaped.
                "title": html.unescape(str(requisition.get("displayJobTitle") or "")).strip(),
                "location": named[0] if named else "",
                "additional_locations": "; ".join(named[1:]),
                "country": places[0].get("country", "") if places and isinstance(places[0], dict) else "",
                "published": posted,
                "deadline": expires,
                "url": f"https://{corp}.csod.com/ux/ats/careersite/{site}/home/requisition/{rid}?c={corp}",
                "description": _without_css(
                    html_to_text(html.unescape(requisition.get("externalDescription") or ""))
                ),
            }
        )
    return Collected(
        records, total, sum(1 for r in records if not r["description"]), confirmed_empty=True
    )


# ------------------------------------------------------ Oracle Recruiting Cloud


def _oracle_detail(host: str, site: str, requisition_id: str) -> dict[str, Any]:
    url = (
        f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
        f'?expand=all&onlyData=true&finder=ById;Id="{requisition_id}",siteNumber={site}'
    )
    try:
        return (request_json(url).get("items") or [{}])[0]
    except RuntimeError as exc:
        print(f"  ! no detail for {requisition_id}: {exc}", file=sys.stderr)
        return {}


def _oracle_advert(detail: dict[str, Any]) -> str:
    parts = [
        html_to_text(detail.get(key) or "")
        for key in (
            "ExternalDescriptionStr",
            "ExternalResponsibilitiesStr",
            "ExternalQualificationsStr",
            "CorporateDescriptionStr",
        )
    ]
    return "\n\n".join(part for part in parts if part)


def oracle_hcm(cfg: dict[str, Any]) -> Collected:
    """`host` (<pod>.fa.<region>.oraclecloud.com), `site` (CX_1 …); optional `query`, `lang`,
    `location_id` (the locationId a link filtered by country carries: Nexi's Italy).

    Oracle Recruiting Cloud's candidate site reads a public REST resource:
    recruitingCEJobRequisitions for the list (with TotalJobsCount), and
    recruitingCEJobRequisitionDetails for each advert. The list only carries
    the requisitions when asked to expand them, and rejects an empty expand.
    """
    host, site = cfg["host"], cfg["site"]
    lang = cfg.get("lang", "en")
    keyword = f',keyword="{cfg["query"]}"' if cfg.get("query") else ""
    if cfg.get("location_id"):
        keyword += f",locationId={cfg['location_id']}"
    found: dict[str, dict[str, Any]] = {}
    total = 0
    # Sorted by posting date, many share a date and the order reshuffles
    # between pages (Fortinet: 975 of 976 in one pass), so the walk is
    # repeated until every requisition has been seen.
    notes: list[str] = []
    for walk in range(4):
        offset = 0
        entries = 0
        # Later walks change the order, so ties fall differently.
        order = ("POSTING_DATES_DESC", "RELEVANCY", "POSTING_DATES_ASC", "RELEVANCY")[walk]
        for _ in range(MAX_PAGES):
            finder = (
                f"findReqs;siteNumber={site},limit=25,offset={offset},sortBy={order}{keyword}"
            )
            payload = request_json(
                f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
                f"?onlyData=true&expand=requisitionList.secondaryLocations"
                f"&finder={urllib.parse.quote(finder, safe=';=,')}"
            )
            item = (payload.get("items") or [{}])[0]
            total = int(item.get("TotalJobsCount") or total)
            page = item.get("requisitionList") or []
            for requisition in page:
                found.setdefault(str(requisition.get("Id")), requisition)
            offset += len(page)
            entries += len(page)
            if not page or offset >= total:
                break
        if len(found) >= total:
            break
        if entries >= total and walk:
            # A whole walk returned `total` entries, yet fewer distinct
            # requisitions: the count includes one listed twice (Fortinet
            # 976 entries, 975 requisitions in every order). Nothing is missing.
            notes.append(f"{total} entries list {len(found)} distinct requisitions")
            total = len(found)
            break

    def build(pair: tuple[str, dict[str, Any]]) -> dict[str, Any]:
        rid, requisition = pair
        detail = _oracle_detail(host, site, rid)
        others = [place.get("Name", "") for place in requisition.get("secondaryLocations") or []]
        return {
            "job_id": rid,
            "title": str(requisition.get("Title") or "").strip(),
            "location": requisition.get("PrimaryLocation") or "",
            "additional_locations": "; ".join(place for place in others if place),
            "country": requisition.get("PrimaryLocationCountry") or "",
            "department": detail.get("JobFamily") or detail.get("Organization") or "",
            "remote_type": requisition.get("WorkplaceType") or "",
            "published": _iso(requisition.get("PostedDate") or detail.get("ExternalPostedStartDate")),
            "deadline": _iso(detail.get("ExternalPostedEndDate")),
            "url": f"https://{host}/hcmUI/CandidateExperience/{lang}/sites/{site}/job/{rid}",
            "description": _oracle_advert(detail),
        }

    records = _pool(build, list(found.items()))
    return Collected(
        records, total, sum(1 for r in records if not r["description"]), notes, confirmed_empty=True
    )


# ----------------------------------------------------------------------- Avature

_AVATURE_RESULT_RE = re.compile(
    r'<h3[^>]*article__header__text__title[^>]*>\s*<a[^>]+href="([^"]+/JobDetail/[^"]+)"[^>]*>(.*?)</a>', re.S
)
_AVATURE_TOTAL_RE = re.compile(r"\d+\s*-\s*\d+\s+(?:of|von|de|sur|di)\s+(\d+)")


def avature(cfg: dict[str, Any]) -> Collected:
    """`search` (…/SearchJobs/<query>), `offset_param` (jobOffset or folderOffset), `size_param`.

    Avature renders its results server-side, a page at a time ("1 - 6 of 60
    results"), and takes an offset parameter whose name differs per site; the
    page size is raised so few pages are needed. Its RSS feed is cut at 20
    and carries no advert, so it only serves to confirm an empty search.
    """
    search = cfg["search"].rstrip("/")
    offset_param = cfg.get("offset_param", "jobOffset")
    size_param = cfg.get("size_param", "jobRecordsPerPage")
    found: dict[str, str] = {}
    total = 0
    for _ in range(MAX_PAGES):
        page = request_text(f"{search}?listFilterMode=1&{size_param}=50&{offset_param}={len(found)}")
        match = _AVATURE_TOTAL_RE.search(_clean(page))
        total = int(match.group(1)) if match else total
        before = len(found)
        for url, title in _AVATURE_RESULT_RE.findall(page):
            found.setdefault(html.unescape(url), _clean(title))
        if len(found) == before or len(found) >= total:
            break
    empty_confirmed = False
    if not found:
        feed = request_text(f"{search}/feed/?listFilterMode=1")
        empty_confirmed = "<rss" in feed and "<item>" not in feed

    def detail(pair: tuple[str, str]) -> dict[str, Any]:
        url, title = pair
        try:
            page = request_text(url)
        except RuntimeError as exc:
            print(f"  ! no detail for {url}: {exc}", file=sys.stderr)
            page = ""
        # Each fact is a field block with a label and a value; the advert is
        # a field block of its own with no label.
        plain: dict[str, str] = {}
        unlabelled: list[str] = []
        for block in inner_html(page, "class", "article__content__view__field", token=True):
            label = _clean(next(iter(inner_html(block, "class", "article__content__view__field__label", token=True)), ""))
            value = next(iter(inner_html(block, "class", "article__content__view__field__value", token=True)), block)
            if label:
                plain[label.rstrip(":")] = html_to_text(value)
            else:
                unlabelled.append(value)
        advert = max(unlabelled, key=len, default="")
        places = [line.lstrip("- ").strip() for line in (plain.get("Location(s)") or plain.get("Location") or "").splitlines() if line.strip()]
        posted = plain.get("Posted since") or plain.get("Posting date") or plain.get("Date posted") or ""
        try:
            posted = datetime.strptime(posted, "%d-%b-%Y").date().isoformat()
        except ValueError:
            posted = _iso(_dmy(posted))
        return {
            "job_id": url.rstrip("/").rsplit("/", 1)[-1],
            "title": title,
            "location": places[0] if places else "",
            "additional_locations": "; ".join(places[1:]),
            "department": plain.get("Organization") or plain.get("Directorate") or plain.get("Business area") or "",
            "contract": plain.get("Employment type") or plain.get("Type of contract") or plain.get("Job type") or "",
            "remote_type": plain.get("Work mode", ""),
            "published": posted,
            "deadline": _iso(_dmy(plain.get("Deadline for applications") or plain.get("Closing date") or plain.get("Deadline") or "")),
            "url": url,
            "description": html_to_text(advert),
        }

    records = _pool(detail, list(found.items()))
    return Collected(
        records,
        total or len(found),
        sum(1 for r in records if not r["description"]),
        confirmed_empty=empty_confirmed,
    )


def _dmy(text: str) -> str:
    """dd/mm/yyyy or dd.mm.yyyy to yyyy-mm-dd, if that is what it is."""
    match = re.search(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", text)
    return f"{match.group(3)}-{int(match.group(2)):02d}-{int(match.group(1)):02d}" if match else text


# ------------------------------------------------- SuccessFactors (classic RMK)

_SF_LINK_RE = re.compile(
    r'<a[^>]+class="jobTitle-link[^"]*"[^>]*href="([^"]+/(\d+)/?)"[^>]*>(.*?)</a>'
    r'|<a[^>]+href="([^"]+/(\d+)/?)"[^>]*class="jobTitle-link[^"]*"[^>]*>(.*?)</a>',
    re.S,
)
#: "Results 1 – 25 of 144", "Showing 1 to 21 of 21 Jobs", "Ergebnisse 1 – 25 von 56", …
_SF_TOTAL_RE = re.compile(
    r"\b\d+\s*(?:–|-|to|a|bis|à|al|t/m|til)\s*\d+\s*(?:of|von|de|di|sur|av|z|van|af)\s*(\d+)"
)
_SF_PROPERTY_RE = re.compile(r'data-careersite-propertyid="([^"]+)"[^>]*>(.*?)</', re.S)
_SF_POSTED_RE = re.compile(r'itemprop="datePosted" content="([^"]+)"')


def sf_classic(cfg: dict[str, Any]) -> Collected:
    """`search`: the search URL as the user follows it (host + path + query string).

    The classic SuccessFactors career site (Recruiting Marketing) renders 25
    results a page, as a table or as tiles depending on the theme, with
    "Results 1 – 25 of 144" in the site's language; `startrow` pages. Each
    posting page has the advert in itemprop="description", its facts in
    data-careersite-propertyid fields, and the posting date in a meta tag.
    """
    parts = urllib.parse.urlsplit(cfg["search"])
    origin = f"{parts.scheme}://{parts.netloc}"
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True) if k != "startrow"]
    found: dict[str, tuple[str, str]] = {}
    total = 0
    stated = False
    for _ in range(MAX_PAGES):
        url = urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query + [("startrow", len(found))]), "")
        )
        page = request_text(url)
        match = _SF_TOTAL_RE.search(_clean(page))
        if match:
            total, stated = int(match.group(1)), True
        before = len(found)
        for groups in _SF_LINK_RE.findall(page):
            link, job_id, title = groups[0:3] if groups[0] else groups[3:6]
            found.setdefault(job_id, (urllib.parse.urljoin(origin, html.unescape(link)), _clean(title)))
        if len(found) == before or (stated and len(found) >= total):
            break
    if not stated:
        total = len(found)
    # A results page that says there are none is the site's own word.
    nothing = bool(re.search(r"\b0\s+(?:results|jobs|Ergebnisse|ofertas|résultats)|no (?:jobs|results)|keine|ninguna|aucun", _clean(page), re.I))

    def detail(item: tuple[str, tuple[str, str]]) -> dict[str, Any]:
        job_id, (url, title) = item
        try:
            page = request_text(url)
        except RuntimeError as exc:
            print(f"  ! no detail for {url}: {exc}", file=sys.stderr)
            page = ""
        props: dict[str, str] = {}
        for key, value in _SF_PROPERTY_RE.findall(page):
            if key != "description":
                props.setdefault(key, _clean(value))
        posted = ""
        match = _SF_POSTED_RE.search(page)
        if match:
            try:
                posted = datetime.strptime(match.group(1), "%a %b %d %H:%M:%S %Z %Y").date().isoformat()
            except ValueError:
                posted = _iso(match.group(1))
        return {
            "job_id": job_id,
            "title": props.get("title") or title,
            "location": props.get("location") or props.get("city") or "",
            "department": props.get("department") or props.get("businessunit") or props.get("dept") or "",
            "published": posted,
            "url": url,
            "description": html_to_text(next(iter(inner_html(page, "itemprop", "description")), "")),
        }

    records = _pool(detail, list(found.items()))
    return Collected(
        records,
        total,
        sum(1 for r in records if not r["description"]),
        confirmed_empty=nothing,
    )


# -------------------------------------------------------------------- Talentsoft

#: One posting on a results page, in either theme (list items or cards): the
#: title link, then the facts list "Réf. : … | 03/10/2026 | CDI | Seclin".
_TS_CARD_RE = re.compile(
    r'class="ts-offer-(?:list-item|card)__title-link[^"]*"\s+href="([^"]+_(\d+)\.aspx)"[^>]*>(.*?)</a>(.*?)</ul>',
    re.S,
)
#: "(87 offres, page 1)" in the page title, whatever the language.
_TS_TOTAL_RE = re.compile(r"\((\d+)\s+\w+,\s*\w+\s+\d+\)")


def talentsoft(cfg: dict[str, Any]) -> Collected:
    """`host` (<client>-cand.talent-soft.com, <client>.profils.org); optional `lcid`.

    Talentsoft (Cegid) career sites list every offer ten a page at
    /offre-de-emploi/liste-offres.aspx?page=N, the total in the page title.
    Each card carries the reference, publication date, contract and place;
    the posting page has the advert in ts-offer-page__content-details.
    """
    origin = f"https://{cfg['host']}"
    lcid = cfg.get("lcid", "1036")
    found: dict[str, dict[str, Any]] = {}
    total = 0
    for number in range(1, MAX_PAGES):
        page = request_text(f"{origin}/offre-de-emploi/liste-offres.aspx?page={number}&LCID={lcid}")
        match = _TS_TOTAL_RE.search(page)
        if match:
            total = int(match.group(1))
        before = len(found)
        for link, job_id, title, rest in _TS_CARD_RE.findall(page):
            facts = [_clean(item) for item in re.findall(r"<li[^>]*>(.*?)</li>", rest, re.S)]
            reference = next((f.split(":", 1)[1].strip() for f in facts if f.startswith("Réf")), "")
            dates = [f for f in facts if re.fullmatch(r"\d{2}/\d{2}/\d{4}", f)]
            others = [f for f in facts if not f.startswith("Réf") and f not in dates]
            found.setdefault(
                job_id,
                {
                    "job_id": reference or job_id,
                    "title": _clean(title),
                    "contract": others[0] if len(others) > 1 else "",
                    "location": others[-1] if others else "",
                    "published": _dmy(dates[0]) if dates else "",
                    "url": urllib.parse.urljoin(origin, html.unescape(link)),
                },
            )
        if len(found) == before or (total and len(found) >= total):
            break
    if not total:
        total = len(found)

    def detail(record: dict[str, Any]) -> dict[str, Any]:
        try:
            page = request_text(record["url"])
        except RuntimeError as exc:
            print(f"  ! no detail for {record['url']}: {exc}", file=sys.stderr)
            page = ""
        text = html_to_text(next(iter(inner_html(page, "class", "ts-offer-page__content-details", token=True)), ""))
        # Some themes leave the date off the card (CEA); the page's meta
        # description still says "Date : 25/09/2026. Ref : …".
        dated = re.search(r"Date : (\d{2}/\d{2}/\d{4})\. Ref", page)
        published = record["published"] or (_dmy(dated.group(1)) if dated else "")
        return {
            **record,
            "published": published,
            "description": re.sub(r"^\s*Informations générales\s*", "", text),
        }

    records = _pool(detail, list(found.values()))
    return Collected(records, total, sum(1 for r in records if not r["description"]))


# ----------------------------------------------------------------------- Algolia


def algolia_search(app_id: str, api_key: str, index: str, query: str = "", facet_filters=None) -> tuple[list[dict[str, Any]], int]:
    """Every hit of an Algolia index for a query and facet filters, and nbHits.

    Several career sites (DNV) list their postings from Algolia with a
    search-only key published in the page; that is the key used here.
    """
    hits: list[dict[str, Any]] = []
    total = 0
    headers = {"X-Algolia-Application-Id": app_id, "X-Algolia-API-Key": api_key}
    for number in range(MAX_PAGES):
        params = urllib.parse.urlencode(
            {
                "query": query,
                "hitsPerPage": 200,
                "page": number,
                "facetFilters": json.dumps(facet_filters or []),
            }
        )
        payload = request_json(
            f"https://{app_id}-dsn.algolia.net/1/indexes/{index}/query", {"params": params}, headers=headers
        )
        total = int(payload.get("nbHits") or 0)
        hits.extend(payload.get("hits") or [])
        if number + 1 >= int(payload.get("nbPages") or 0):
            break
    return hits, total


PLATFORMS = {
    "eightfold": eightfold,
    "csod": csod,
    "oracle_hcm": oracle_hcm,
    "avature": avature,
    "sf_classic": sf_classic,
    "talentsoft": talentsoft,
}

if __name__ == "__main__":  # python3 ats_enterprise.py eightfold '{"host": …}'
    result = PLATFORMS[sys.argv[1]](json.loads(sys.argv[2]))
    print(result.total, len(result.records), result.missing_detail, result.notes)
    for record in result.records[:3]:
        print({k: str(v)[:60] for k, v in record.items()})

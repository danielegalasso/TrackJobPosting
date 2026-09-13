"""Read postings out of a page's own structured data, with no ATS at all.

The largest single failure on a real 631-organization list was "page rendered,
no ATS link" — 377 of them. Many of those pages are not hiding anything: they
carry their postings as **schema.org JobPosting in JSON-LD**, because that is
what Google for Jobs consumes and every employer wants their roles in it.

That markup is machine-readable and standardised — title, description,
datePosted, jobLocation, employmentType, baseSalary — so reading it is not
scraping a layout that will change next week. It is the same bargain as the
ATS connectors: a published, documented format the page already serves.

Two page shapes are handled:

1. The postings are on the page, as `JobPosting` objects. One request.
2. The page is an index — an `ItemList` of links, or a listing whose adverts
   live one page down. Each linked page is then fetched for its own
   `JobPosting`, bounded by `max_jobs` and paced like any other request.

A page that renders its list with JavaScript and embeds no JSON-LD until it
does is reported as empty rather than guessed at. That is the honest limit of
reading a page over plain HTTP, and it is where a browser would be needed.

The token is the careers page URL.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from ..compensation import parse_compensation
from ..models import RawPosting, TargetSource
from .base import Connector, ConnectorError, iso_date, register, strip_html

_SCRIPT_RE = re.compile(
    r"<script[^>]*type\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
#: How many linked pages to open when the careers page is only an index.
DEFAULT_FOLLOW_LIMIT = 40


def blocks(html: str) -> Iterator[Any]:
    """Every JSON-LD document in a page, skipping the malformed ones."""
    for raw in _SCRIPT_RE.findall(html or ""):
        text = raw.strip()
        if not text:
            continue
        try:
            yield json.loads(text)
        except ValueError:
            # A single broken block must not cost the rest of the page.
            continue


def _types(node: Any) -> set[str]:
    raw = node.get("@type") if isinstance(node, dict) else None
    if isinstance(raw, str):
        return {raw.lower()}
    if isinstance(raw, list):
        return {str(item).lower() for item in raw}
    return set()


def walk(node: Any) -> Iterator[dict[str, Any]]:
    """Every JobPosting in a JSON-LD document, however it is nested.

    Pages wrap them in `@graph`, in `ItemList.itemListElement`, in bare
    arrays, and inside `mainEntity`; all of those are the same posting.
    """
    if isinstance(node, list):
        for item in node:
            yield from walk(item)
        return
    if not isinstance(node, dict):
        return
    if "jobposting" in _types(node):
        yield node
        return
    for key in ("@graph", "itemListElement", "mainEntity", "item", "hasPart"):
        if key in node:
            yield from walk(node[key])


def posting_links(html: str, page_url: str, limit: int) -> list[str]:
    """Where an index page says its postings are.

    Read from the page's own ItemList, which is the standard way to publish a
    list of postings, rather than guessed from the markup.
    """
    found: list[str] = []
    seen: set[str] = set()
    for document in blocks(html):
        for url in _item_urls(document):
            absolute = urljoin(page_url, url)
            if absolute in seen:
                continue
            seen.add(absolute)
            found.append(absolute)
            if len(found) >= limit:
                return found
    return found


def _item_urls(node: Any) -> Iterator[str]:
    if isinstance(node, list):
        for item in node:
            yield from _item_urls(item)
        return
    if not isinstance(node, dict):
        return
    types = _types(node)
    if "itemlist" in types:
        for element in node.get("itemListElement") or []:
            yield from _item_urls(element)
        return
    if "listitem" in types:
        target = node.get("item") or node.get("url")
        if isinstance(target, str):
            yield target
        elif isinstance(target, dict):
            url = target.get("url") or target.get("@id")
            if isinstance(url, str):
                yield url
        return
    for key in ("@graph", "mainEntity", "hasPart"):
        if key in node:
            yield from _item_urls(node[key])


@register
class JsonLdConnector(Connector):
    source_type = "jsonld"
    token_hint = "The careers page URL, e.g. https://acme.com/careers"

    def _get_html(self, url: str) -> str:
        self._throttle()
        try:
            response = self.client.get(url, headers={"Accept": "text/html"})
        except httpx.HTTPError as exc:
            raise ConnectorError(f"{url}: {exc}") from exc
        if response.status_code >= 400:
            raise ConnectorError(f"{url}: HTTP {response.status_code}")
        return response.text

    def fetch(self, target: TargetSource) -> Iterable[RawPosting]:
        page_url = target.board_token.strip()
        if "://" not in page_url:
            page_url = f"https://{page_url}"
        html = self._get_html(page_url)

        postings: list[tuple[dict[str, Any], str]] = [
            (node, page_url) for document in blocks(html) for node in walk(document)
        ]

        if not postings:
            # The page is an index: its postings live one page down, and it
            # says where in its own ItemList.
            links = posting_links(html, page_url, DEFAULT_FOLLOW_LIMIT)
            for link in links[: self.max_jobs]:
                try:
                    inner = self._get_html(link)
                except ConnectorError as exc:
                    self.log(f"jsonld: {exc}", "warning")
                    continue
                for document in blocks(inner):
                    for node in walk(document):
                        postings.append((node, link))

        if not postings:
            raise ConnectorError(
                f"{page_url}: no JobPosting structured data found. The page may "
                "build its list with JavaScript, which plain HTTP cannot see."
            )

        self.log(f"jsonld/{urlparse(page_url).netloc}: {len(postings)} postings listed")
        kept = [
            (node, source)
            for node, source in postings
            if self.wanted(str(node.get("title") or ""))
        ]
        self.note_truncation(f"jsonld/{urlparse(page_url).netloc}", len(kept))

        for node, source in kept[: self.max_jobs]:
            description = strip_html(str(node.get("description") or ""))
            amount, currency, rate = _pay(node)
            if amount is None:
                amount, currency, rate = parse_compensation(description[:4000])
            yield RawPosting(
                external_id=_identifier(node, source),
                company=target.company,
                title=str(node.get("title") or "Untitled role").strip(),
                location=_location(node),
                apply_url=str(node.get("url") or source),
                description=description,
                date_posted=iso_date(node.get("datePosted")),
                source_type=self.source_type,
                amount=amount,
                currency=currency,
                rate=rate,
            )


_UNIT_TO_RATE = {
    "HOUR": "Hourly",
    "DAY": "Daily",
    "WEEK": "Weekly",
    "MONTH": "Monthly",
    "YEAR": "Yearly",
}


def _pay(node: dict[str, Any]) -> tuple[float | None, str | None, str | None]:
    """schema.org baseSalary, when the employer published one."""
    salary = node.get("baseSalary")
    if not isinstance(salary, dict):
        return None, None, None
    currency = str(salary.get("currency") or "").upper() or None
    value = salary.get("value")
    if isinstance(value, dict):
        currency = str(value.get("currency") or currency or "").upper() or None
        unit = str(value.get("unitText") or "").upper()
        for key in ("value", "minValue"):
            raw = value.get(key)
            if isinstance(raw, int | float) and raw:
                return float(raw), currency, _UNIT_TO_RATE.get(unit, "Yearly")
            if isinstance(raw, str):
                try:
                    return float(raw.replace(",", "")), currency, _UNIT_TO_RATE.get(unit, "Yearly")
                except ValueError:
                    continue
    elif isinstance(value, int | float) and value:
        return float(value), currency, "Yearly"
    return None, None, None


def _location(node: dict[str, Any]) -> str:
    places = node.get("jobLocation")
    if isinstance(places, dict):
        places = [places]
    names: list[str] = []
    for place in places or []:
        if not isinstance(place, dict):
            continue
        address = place.get("address")
        if isinstance(address, str):
            names.append(address.strip())
            continue
        if not isinstance(address, dict):
            continue
        parts = [
            address.get("addressLocality"),
            address.get("addressRegion"),
            address.get("addressCountry"),
        ]
        flat = [
            (part.get("name") if isinstance(part, dict) else part) for part in parts
        ]
        joined = ", ".join(str(part).strip() for part in flat if str(part or "").strip())
        if joined:
            names.append(joined)
    remote = str(node.get("jobLocationType") or "").upper() == "TELECOMMUTE"
    if not names:
        return "Remote" if remote else "Not specified"
    primary = names[0]
    if remote:
        primary = f"Remote, {primary}"
    return f"{primary} +{len(names) - 1} more" if len(names) > 1 else primary


def _identifier(node: dict[str, Any], source: str) -> str:
    raw = node.get("identifier")
    if isinstance(raw, dict):
        value = raw.get("value") or raw.get("name")
        if value:
            return str(value)
    elif isinstance(raw, str | int) and raw:
        return str(raw)
    url = str(node.get("url") or source)
    return url.rstrip("/").rsplit("/", 1)[-1] or url

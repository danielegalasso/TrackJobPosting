"""Teamtailor careers-site connector.

Endpoint: https://{token}/jobs.rss?per_page=200

Teamtailor's own support documentation describes this: any Teamtailor-built
career site serves its published jobs as RSS by appending `.rss` to the jobs
page, and it accepts `per_page` and `offset`. It is public; the authenticated
API at api.teamtailor.com needs a key minted per tenant, which is useless for
watching many employers at once.

An earlier version of this connector used `/jobs.json`, which is widely
repeated by scraper vendors and does not exist: on a real list it found ten
tenants and every single one refused. RSS carries metadata rather than the
full advert, so descriptions are shorter here than from a JSON board — the
trade for an endpoint that actually answers.

The token is either a tenant (`acme` → acme.teamtailor.com) or a full host,
because many career sites run on the employer's own domain
(`careers.sateliot.com`, `aerospace-jobs.sener`).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from xml.etree import ElementTree

import httpx

from ..compensation import parse_compensation
from ..models import RawPosting, TargetSource
from .base import Connector, ConnectorError, iso_date, register, strip_html

#: The feed's own maximum useful page; it accepts per_page freely.
PAGE_SIZE = 200


def feed_url(token: str) -> str:
    """Where this career site's RSS lives."""
    host = token.strip().rstrip("/")
    if "://" in host:
        host = host.split("://", 1)[1]
    if "." not in host:
        host = f"{host}.teamtailor.com"
    return f"https://{host}/jobs.rss"


@register
class TeamtailorConnector(Connector):
    source_type = "teamtailor"
    token_hint = "Career site host, e.g. acme (for acme.teamtailor.com) or careers.acme.com"

    def fetch(self, target: TargetSource) -> Iterable[RawPosting]:
        url = feed_url(target.board_token)
        self._throttle()
        try:
            response = self.client.get(url, params={"per_page": str(PAGE_SIZE)})
        except httpx.HTTPError as exc:
            raise ConnectorError(f"{url}: {exc}") from exc
        if response.status_code == 404:
            raise ConnectorError(
                f"{url}: no RSS feed (404) — check the career-site host, and that "
                "the site is Teamtailor-built"
            )
        if response.status_code >= 400:
            raise ConnectorError(f"{url}: HTTP {response.status_code}")

        try:
            root = ElementTree.fromstring(response.text)
        except ElementTree.ParseError as exc:
            raise ConnectorError(f"{url}: response was not valid RSS ({exc})") from exc

        items = root.findall(".//item")
        self.log(f"teamtailor/{target.board_token}: {len(items)} postings listed")

        items = [i for i in items if self.wanted(_text(i, "title"))]
        self.note_truncation(f"teamtailor/{target.board_token}", len(items))

        for item in items[: self.max_jobs]:
            link = _text(item, "link")
            description = strip_html(_text(item, "description"))
            amount, currency, rate = parse_compensation(description[:4000])
            yield RawPosting(
                external_id=_external_id(item, link),
                company=target.company,
                title=_text(item, "title") or "Untitled role",
                # Only an explicit location is used. Titles read "Role - City"
                # often enough to be tempting, but "Head of Engineering -
                # Platform" is not a place, and a wrong location is worse than
                # none: the portal filters on this field, so a guess both
                # displays nonsense and matches nothing.
                location=_location(item) or "Not specified",
                apply_url=link or url,
                description=description,
                date_posted=iso_date(_text(item, "pubDate")),
                source_type=self.source_type,
                amount=amount,
                currency=currency,
                rate=rate,
            )


def _text(element: Any, tag: str) -> str:
    found = element.find(tag)
    return (found.text or "").strip() if found is not None and found.text else ""


def _location(item: Any) -> str:
    """Some feeds carry a location element in a namespace of their own."""
    for child in item:
        tag = child.tag.rsplit("}", 1)[-1].lower()
        if tag in ("location", "city", "joblocation") and (child.text or "").strip():
            return child.text.strip()
    return ""


def _external_id(item: Any, link: str) -> str:
    guid = _text(item, "guid")
    if guid:
        return guid.rsplit("/", 1)[-1]
    return link.rsplit("/", 1)[-1] if link else ""

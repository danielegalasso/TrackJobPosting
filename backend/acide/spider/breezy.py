"""Breezy HR careers-board connector.

Endpoint: https://{token}.breezy.hr/json?verbose=true

Breezy's own career boards read this, and it is public — no key, no browser.
`verbose=true` returns the full advert with the listing, so a whole board is
one request.

This is the connector the Telespazio Belgium case needed: its careers page at
telespazio.be/en/careers carries a "Discover our positions here" button
pointing at telespazio-be.breezy.hr, so once Breezy is recognised the link on
the page is enough and nothing has to be clicked.

Breezy also publishes an authenticated v3 API. It is not used here: it needs a
token per customer, which is no use for watching many employers.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..compensation import parse_compensation
from ..models import RawPosting, TargetSource
from .base import Connector, iso_date, register, strip_html


def board_url(token: str) -> str:
    host = token.strip().rstrip("/")
    if "://" in host:
        host = host.split("://", 1)[1]
    if "." not in host:
        host = f"{host}.breezy.hr"
    return f"https://{host}/json"


@register
class BreezyConnector(Connector):
    source_type = "breezy"
    token_hint = "Careers subdomain from <name>.breezy.hr"

    def fetch(self, target: TargetSource) -> Iterable[RawPosting]:
        url = board_url(target.board_token)
        payload = self.get_json(url, params={"verbose": "true"})
        positions = _positions(payload)
        self.log(f"breezy/{target.board_token}: {len(positions)} postings listed")

        positions = [p for p in positions if self.wanted(str(p.get("name") or ""))]
        self.note_truncation(f"breezy/{target.board_token}", len(positions))

        base = url.rsplit("/json", 1)[0]
        for entry in positions[: self.max_jobs]:
            description = strip_html(
                str(entry.get("description") or entry.get("requirements") or "")
            )
            amount, currency, rate = parse_compensation(description[:4000])
            identifier = str(entry.get("id") or entry.get("_id") or "")
            yield RawPosting(
                external_id=identifier,
                company=target.company,
                title=str(entry.get("name") or "Untitled role").strip(),
                location=_location(entry),
                apply_url=_apply_url(entry, base, identifier),
                description=description,
                date_posted=iso_date(
                    entry.get("published_date")
                    or entry.get("creation_date")
                    or entry.get("updated_date")
                ),
                source_type=self.source_type,
                amount=amount,
                currency=currency,
                rate=rate,
            )


def _positions(payload: object) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("positions", "jobs", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _named(value: object) -> str:
    """Breezy nests most attributes as {"id": …, "name": …}."""
    if isinstance(value, dict):
        return str(value.get("name") or value.get("city") or "").strip()
    return str(value or "").strip()


def _location(entry: dict[str, Any]) -> str:
    location = entry.get("location")
    if isinstance(location, dict):
        parts = [
            _named(location.get("city")) or _named(location.get("name")),
            _named(location.get("state")),
            _named(location.get("country")),
        ]
        joined = ", ".join(part for part in parts if part)
        if location.get("is_remote"):
            return f"Remote, {joined}" if joined else "Remote"
        if joined:
            return joined
    plain = _named(location)
    return plain or "Not specified"


def _apply_url(entry: dict[str, Any], base: str, identifier: str) -> str:
    for key in ("url", "friendly_id", "_id"):
        value = entry.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return value
    friendly = entry.get("friendly_id") or identifier
    return f"{base}/p/{friendly}" if friendly else base

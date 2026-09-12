"""Personio recruiting XML connector.

Endpoint: https://{token}.jobs.personio.de/xml?language=en

Every Personio customer's career site publishes this feed, unauthenticated —
it is what employers embed in their own site. It is XML rather than JSON,
the only connector here that is, so it parses rather than calls get_json.

Some tenants are served from `.jobs.personio.com` instead of `.de`; both are
tried, and a token may also be given as a full host to pin one.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from xml.etree import ElementTree

import httpx

from ..compensation import parse_compensation
from ..models import RawPosting, TargetSource
from .base import Connector, ConnectorError, iso_date, register, strip_html


def feed_urls(token: str) -> list[str]:
    """Where this tenant's XML feed might live, most likely first."""
    if "." in token:  # a full host was configured
        host = token if "://" not in token else token.split("://", 1)[1]
        return [f"https://{host.rstrip('/')}/xml"]
    return [
        f"https://{token}.jobs.personio.de/xml",
        f"https://{token}.jobs.personio.com/xml",
    ]


@register
class PersonioConnector(Connector):
    source_type = "personio"
    token_hint = "Careers subdomain from <name>.jobs.personio.de"

    def _fetch_xml(self, url: str) -> str:
        self._throttle()
        try:
            response = self.client.get(url, params={"language": "en"})
        except httpx.HTTPError as exc:
            raise ConnectorError(f"{url}: {exc}") from exc
        if response.status_code == 404:
            raise ConnectorError(f"{url}: board not found (404) — check the board token")
        if response.status_code >= 400:
            raise ConnectorError(f"{url}: HTTP {response.status_code}")
        return response.text

    def fetch(self, target: TargetSource) -> Iterable[RawPosting]:
        candidates = feed_urls(target.board_token)
        last_error: ConnectorError | None = None
        body = ""
        used = candidates[0]
        for url in candidates:
            try:
                body = self._fetch_xml(url)
                used = url
                break
            except ConnectorError as exc:
                last_error = exc
        if not body:
            raise last_error or ConnectorError(f"{used}: empty feed")

        try:
            root = ElementTree.fromstring(body)
        except ElementTree.ParseError as exc:
            raise ConnectorError(f"{used}: response was not valid XML ({exc})") from exc

        positions = root.findall(".//position")
        self.log(f"personio/{target.board_token}: {len(positions)} postings listed")
        base = used.rsplit("/xml", 1)[0]

        for position in positions[: self.max_jobs]:
            job_id = _text(position, "id")
            description = strip_html(_description(position))
            amount, currency, rate = parse_compensation(description[:4000])
            yield RawPosting(
                external_id=job_id,
                company=target.company,
                title=_text(position, "name") or "Untitled role",
                location=_text(position, "office") or "Not specified",
                apply_url=f"{base}/job/{job_id}" if job_id else base,
                description=description,
                date_posted=iso_date(_text(position, "createdAt")),
                source_type=self.source_type,
                amount=amount,
                currency=currency,
                rate=rate,
            )


def _text(element: Any, tag: str) -> str:
    found = element.find(tag)
    return (found.text or "").strip() if found is not None and found.text else ""


def _description(position: Any) -> str:
    """Personio splits an advert into named blocks; the evaluator wants all."""
    blocks: list[str] = []
    for block in position.findall(".//jobDescription"):
        heading = _text(block, "name")
        body = _text(block, "value")
        if body:
            blocks.append(f"{heading}\n{body}" if heading else body)
    return "\n\n".join(blocks)

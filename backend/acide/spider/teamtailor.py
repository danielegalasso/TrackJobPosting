"""Teamtailor careers-site connector.

Endpoint: https://{token}.teamtailor.com/jobs.json

Teamtailor's official API is per-tenant key-gated, which is useless for
watching many employers — a key from one tenant cannot read another. Every
career site also serves its published board as JSON, which is what is read
here.

The feed's exact envelope varies between career-site versions, so the shapes
are all accepted rather than assumed: a bare list, or an object keyed
`jobs`, `data`, or `positions`, with JSON:API-style `attributes` unwrapped.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..compensation import parse_compensation
from ..models import RawPosting, TargetSource
from .base import Connector, iso_date, register, strip_html


def jobs_url(token: str) -> str:
    return f"https://{token}.teamtailor.com/jobs.json"


@register
class TeamtailorConnector(Connector):
    source_type = "teamtailor"
    token_hint = "Careers subdomain from <name>.teamtailor.com"

    def fetch(self, target: TargetSource) -> Iterable[RawPosting]:
        url = jobs_url(target.board_token)
        entries = _entries(self.get_json(url))
        self.log(f"teamtailor/{target.board_token}: {len(entries)} postings listed")

        for raw in entries[: self.max_jobs]:
            entry = _flatten(raw)
            description = strip_html(
                entry.get("body") or entry.get("description") or entry.get("pitch") or ""
            )
            amount, currency, rate = parse_compensation(description[:4000])
            yield RawPosting(
                external_id=str(entry.get("id") or entry.get("internal-name") or ""),
                company=target.company,
                title=str(entry.get("title") or entry.get("name") or "Untitled role").strip(),
                location=_location(entry),
                apply_url=str(
                    entry.get("careersite-job-url")
                    or entry.get("careersite_job_url")
                    or entry.get("url")
                    or entry.get("apply_url")
                    or url
                ),
                description=description,
                date_posted=iso_date(
                    entry.get("created-at") or entry.get("created_at") or entry.get("published_at")
                ),
                source_type=self.source_type,
                amount=amount,
                currency=currency,
                rate=rate,
            )


def _entries(payload: object) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("jobs", "data", "positions"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _flatten(entry: dict[str, Any]) -> dict[str, Any]:
    """JSON:API nests the useful fields under `attributes`; plain feeds do not."""
    attributes = entry.get("attributes")
    if isinstance(attributes, dict):
        return {"id": entry.get("id"), **attributes}
    return entry


def _location(entry: dict[str, Any]) -> str:
    if entry.get("remote-status") in ("fully", "hybrid") or entry.get("remote"):
        prefix = "Remote"
    else:
        prefix = ""
    for key in ("location", "city", "human-location", "locations"):
        value = entry.get(key)
        if isinstance(value, list):
            value = ", ".join(str(item) for item in value if item)
        if isinstance(value, dict):
            value = value.get("name") or value.get("city")
        text = str(value or "").strip()
        if text:
            return f"{prefix}, {text}" if prefix else text
    return prefix or "Not specified"

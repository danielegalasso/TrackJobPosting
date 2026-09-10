"""Greenhouse job board connector.

Endpoint: https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
Public, documented, returns every live posting for a board in one call.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..compensation import parse_compensation
from ..models import RawPosting, TargetSource
from .base import Connector, iso_date, register, strip_html

BASE_URL = "https://boards-api.greenhouse.io/v1/boards"


@register
class GreenhouseConnector(Connector):
    source_type = "greenhouse"
    token_hint = "Board token from job-boards.greenhouse.io/<token>"

    def fetch(self, target: TargetSource) -> Iterable[RawPosting]:
        url = f"{BASE_URL}/{target.board_token}/jobs"
        payload = self.get_json(url, params={"content": "true"})
        jobs: list[dict[str, Any]] = payload.get("jobs", []) if isinstance(payload, dict) else []
        self.log(f"greenhouse/{target.board_token}: {len(jobs)} postings listed")

        for entry in jobs[: self.max_jobs]:
            description = strip_html(entry.get("content", "") or "")
            amount, currency, rate = parse_compensation(description[:4000])
            yield RawPosting(
                external_id=str(entry.get("id")),
                company=target.company,
                title=(entry.get("title") or "Untitled role").strip(),
                location=_location(entry),
                apply_url=entry.get("absolute_url") or url,
                description=description,
                date_posted=iso_date(entry.get("updated_at") or entry.get("first_published")),
                source_type=self.source_type,
                amount=amount,
                currency=currency,
                rate=rate,
            )


def _location(entry: dict[str, Any]) -> str:
    """Greenhouse exposes one primary location plus optional office rows."""
    primary = (entry.get("location") or {}).get("name", "").strip()
    offices = [
        office.get("name", "").strip()
        for office in entry.get("offices", []) or []
        if office.get("name")
    ]
    extras = [name for name in offices if name and name != primary]
    if primary and extras:
        return f"{primary} +{len(extras)} more"
    return primary or (extras[0] if extras else "Not specified")

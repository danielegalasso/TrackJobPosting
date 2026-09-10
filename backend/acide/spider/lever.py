"""Lever postings connector.

Endpoint: https://api.lever.co/v0/postings/{token}?mode=json
Public JSON mirror of the company's hosted job site.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..compensation import parse_compensation
from ..models import RawPosting, TargetSource
from .base import Connector, iso_date, register, strip_html

BASE_URL = "https://api.lever.co/v0/postings"


@register
class LeverConnector(Connector):
    source_type = "lever"
    token_hint = "Company handle from jobs.lever.co/<handle>"

    def fetch(self, target: TargetSource) -> Iterable[RawPosting]:
        url = f"{BASE_URL}/{target.board_token}"
        payload = self.get_json(url, params={"mode": "json"})
        postings: list[dict[str, Any]] = payload if isinstance(payload, list) else []
        self.log(f"lever/{target.board_token}: {len(postings)} postings listed")

        for entry in postings[: self.max_jobs]:
            description = strip_html(
                entry.get("descriptionPlain") or entry.get("description") or ""
            )
            # Lever splits requirements/responsibilities into `lists`, where
            # `text` is the heading and `content` the HTML body.
            extras = "\n".join(
                "\n".join(
                    (
                        (section.get("text") or "").strip(),
                        strip_html(section.get("content", "")),
                    )
                ).strip()
                for section in entry.get("lists", []) or []
            )
            full_text = f"{description}\n{extras}".strip()
            amount, currency, rate = parse_compensation(
                f"{entry.get('salaryRange') or ''} {full_text[:4000]}"
            )
            yield RawPosting(
                external_id=str(entry.get("id")),
                company=target.company,
                title=(entry.get("text") or "Untitled role").strip(),
                location=_location(entry),
                apply_url=entry.get("hostedUrl") or entry.get("applyUrl") or url,
                description=full_text,
                date_posted=iso_date(entry.get("createdAt")),
                source_type=self.source_type,
                amount=amount,
                currency=currency,
                rate=rate,
            )


def _location(entry: dict[str, Any]) -> str:
    """Lever nests the location under `categories`, with optional extra sites."""
    categories = entry.get("categories") or {}
    primary = (categories.get("location") or "").strip()
    all_locations = categories.get("allLocations")
    if isinstance(all_locations, list):
        others = [
            str(loc).strip()
            for loc in all_locations
            if str(loc).strip() and str(loc).strip() != primary
        ]
        if primary and others:
            return f"{primary} +{len(others)} more"
        if not primary and others:
            return others[0]
    workplace = (entry.get("workplaceType") or "").strip()
    if primary and workplace and workplace.lower() not in primary.lower():
        return f"{primary} ({workplace})"
    return primary or workplace or "Not specified"

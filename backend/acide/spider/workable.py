"""Workable careers-widget connector.

Endpoint: https://apply.workable.com/api/v1/widget/accounts/{token}?details=true

This is the feed Workable-hosted careers pages and the embeddable widget
read. It is public and unauthenticated; `details=true` asks for the full
advert rather than titles alone.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..compensation import parse_compensation
from ..models import RawPosting, TargetSource
from .base import Connector, iso_date, register, strip_html

BASE_URL = "https://apply.workable.com/api/v1/widget/accounts"


@register
class WorkableConnector(Connector):
    source_type = "workable"
    token_hint = "Account slug from apply.workable.com/<slug>"

    def fetch(self, target: TargetSource) -> Iterable[RawPosting]:
        url = f"{BASE_URL}/{target.board_token}"
        payload = self.get_json(url, params={"details": "true"})
        jobs: list[dict[str, Any]] = payload.get("jobs", []) if isinstance(payload, dict) else []
        self.log(f"workable/{target.board_token}: {len(jobs)} postings listed")

        for entry in jobs[: self.max_jobs]:
            description = strip_html(
                "\n\n".join(
                    part
                    for part in (
                        entry.get("description"),
                        entry.get("requirements"),
                        entry.get("benefits"),
                    )
                    if part
                )
            )
            amount, currency, rate = parse_compensation(description[:4000])
            yield RawPosting(
                external_id=str(entry.get("shortcode") or entry.get("id") or ""),
                company=target.company,
                title=(entry.get("title") or "Untitled role").strip(),
                location=_location(entry),
                apply_url=(
                    entry.get("application_url")
                    or entry.get("url")
                    or entry.get("shortlink")
                    or url
                ),
                description=description,
                date_posted=iso_date(entry.get("published_on") or entry.get("created_at")),
                source_type=self.source_type,
                amount=amount,
                currency=currency,
                rate=rate,
            )


def _location(entry: dict[str, Any]) -> str:
    location = entry.get("location")
    if isinstance(location, dict):
        parts = [location.get("city"), location.get("region"), location.get("country")]
        if location.get("workplace_type") == "remote" or location.get("telecommuting"):
            parts = ["Remote", *parts]
    else:
        parts = [entry.get("city"), entry.get("state"), entry.get("country")]
        if entry.get("telecommuting"):
            parts = ["Remote", *parts]
    joined = ", ".join(str(part).strip() for part in parts if str(part or "").strip())
    return joined or "Not specified"

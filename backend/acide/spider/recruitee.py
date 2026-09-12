"""Recruitee careers-site connector.

Endpoint: https://{token}.recruitee.com/api/offers/

Recruitee runs two APIs. `api.recruitee.com` is the ATS API and needs a
per-customer token. The *careers-site* API, served from the customer's own
careers host, is the one their public career page reads — published postings
only, no key. That is the one used here.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..compensation import parse_compensation
from ..models import RawPosting, TargetSource
from .base import Connector, iso_date, register, strip_html


def offers_url(token: str) -> str:
    return f"https://{token}.recruitee.com/api/offers/"


@register
class RecruiteeConnector(Connector):
    source_type = "recruitee"
    token_hint = "Careers subdomain from <name>.recruitee.com"

    def fetch(self, target: TargetSource) -> Iterable[RawPosting]:
        url = offers_url(target.board_token)
        payload = self.get_json(url)
        offers: list[dict[str, Any]] = (
            payload.get("offers", []) if isinstance(payload, dict) else []
        )
        self.log(f"recruitee/{target.board_token}: {len(offers)} postings listed")

        for entry in offers[: self.max_jobs]:
            # Recruitee splits the advert across description and requirements;
            # the evaluator wants the whole advert, so both are kept.
            description = strip_html(
                "\n\n".join(
                    part
                    for part in (entry.get("description"), entry.get("requirements"))
                    if part
                )
            )
            amount, currency, rate = parse_compensation(description[:4000])
            yield RawPosting(
                external_id=str(entry.get("id") or entry.get("slug") or ""),
                company=target.company,
                title=(entry.get("title") or "Untitled role").strip(),
                location=_location(entry),
                apply_url=(
                    entry.get("careers_apply_url") or entry.get("careers_url") or url
                ),
                description=description,
                date_posted=iso_date(entry.get("published_at") or entry.get("created_at")),
                source_type=self.source_type,
                amount=amount,
                currency=currency,
                rate=rate,
            )


def _location(entry: dict[str, Any]) -> str:
    if entry.get("remote") and not entry.get("city"):
        return "Remote"
    parts = [
        str(entry.get(field) or "").strip()
        for field in ("city", "state_name", "country")
    ]
    joined = ", ".join(part for part in parts if part)
    return joined or (entry.get("location") or "").strip() or "Not specified"

"""Ashby job board connector.

Endpoint: https://api.ashbyhq.com/posting-api/job-board/{token}
Public posting API; `includeCompensation` asks for structured pay bands
where the employer has chosen to publish them.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..compensation import parse_compensation
from ..models import RawPosting, TargetSource
from .base import Connector, iso_date, register, strip_html

BASE_URL = "https://api.ashbyhq.com/posting-api/job-board"


@register
class AshbyConnector(Connector):
    source_type = "ashby"
    token_hint = "Board name from jobs.ashbyhq.com/<name>"

    def fetch(self, target: TargetSource) -> Iterable[RawPosting]:
        url = f"{BASE_URL}/{target.board_token}"
        payload = self.get_json(url, params={"includeCompensation": "true"})
        jobs: list[dict[str, Any]] = payload.get("jobs", []) if isinstance(payload, dict) else []
        self.log(f"ashby/{target.board_token}: {len(jobs)} postings listed")

        for entry in jobs[: self.max_jobs]:
            description = strip_html(
                entry.get("descriptionPlain") or entry.get("descriptionHtml") or ""
            )
            amount, currency, rate = _compensation(entry, description)
            yield RawPosting(
                external_id=str(entry.get("id")),
                company=target.company,
                title=(entry.get("title") or "Untitled role").strip(),
                location=_location(entry),
                apply_url=entry.get("jobUrl") or entry.get("applyUrl") or url,
                description=description,
                date_posted=iso_date(entry.get("publishedAt") or entry.get("updatedAt")),
                source_type=self.source_type,
                amount=amount,
                currency=currency,
                rate=rate,
            )


_INTERVAL_TO_RATE = {
    "HOURLY": "Hourly",
    "DAILY": "Daily",
    "MONTHLY": "Monthly",
    "YEARLY": "Yearly",
    "PER_YEAR": "Yearly",
    "PER_MONTH": "Monthly",
    "PER_HOUR": "Hourly",
}


def _compensation(
    entry: dict[str, Any], description: str
) -> tuple[float | None, str | None, str | None]:
    """Prefer Ashby's structured salary band; fall back to parsing prose."""
    compensation = entry.get("compensation") or {}
    tiers = compensation.get("compensationTiers") or []
    for tier in tiers:
        for component in tier.get("components", []) or []:
            if (component.get("compensationType") or "").upper() != "SALARY":
                continue
            minimum = component.get("minValue")
            if minimum in (None, 0):
                continue
            rate = _INTERVAL_TO_RATE.get((component.get("interval") or "").upper(), "Yearly")
            currency = (component.get("currencyCode") or "USD").upper()
            return float(minimum), currency, rate
    return parse_compensation(description[:4000])


def _location(entry: dict[str, Any]) -> str:
    primary = (entry.get("location") or "").strip()
    secondary = entry.get("secondaryLocations") or []
    names = [
        (loc.get("location") if isinstance(loc, dict) else str(loc)) or ""
        for loc in secondary
    ]
    extras = [name.strip() for name in names if name.strip() and name.strip() != primary]
    if primary and extras:
        return f"{primary} +{len(extras)} more"
    if not primary and entry.get("isRemote"):
        return "Remote"
    return primary or "Not specified"

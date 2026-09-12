"""SmartRecruiters Posting API connector.

Endpoints:
  https://api.smartrecruiters.com/v1/companies/{token}/postings
  https://api.smartrecruiters.com/v1/companies/{token}/postings/{id}

The documented public Posting API: postings an employer has published are
readable without a key. The list carries no advert text, so the detail
endpoint is called per posting — bounded by max_jobs, and a posting whose
detail cannot be read is still yielded with what the list gave.

Not every customer has the public feed enabled; for those the list answers
404 and the target is reported as an error rather than silently empty.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..compensation import parse_compensation
from ..models import RawPosting, TargetSource
from .base import Connector, ConnectorError, iso_date, register, strip_html

BASE_URL = "https://api.smartrecruiters.com/v1/companies"
#: The API caps a page at 100 postings.
PAGE_SIZE = 100


@register
class SmartRecruitersConnector(Connector):
    source_type = "smartrecruiters"
    token_hint = "Company identifier from careers.smartrecruiters.com/<Company>"

    def fetch(self, target: TargetSource) -> Iterable[RawPosting]:
        listing = f"{BASE_URL}/{target.board_token}/postings"
        entries: list[dict[str, Any]] = []
        offset = 0
        while len(entries) < self.max_jobs:
            payload = self.get_json(
                listing, params={"limit": str(PAGE_SIZE), "offset": str(offset)}
            )
            page = payload.get("content", []) if isinstance(payload, dict) else []
            if not page:
                break
            entries.extend(item for item in page if isinstance(item, dict))
            offset += len(page)
            if len(page) < PAGE_SIZE:
                break
        self.log(f"smartrecruiters/{target.board_token}: {len(entries)} postings listed")

        for entry in entries[: self.max_jobs]:
            posting_id = str(entry.get("id") or entry.get("uuid") or "")
            description = ""
            if posting_id:
                description = self._advert(listing, posting_id)
            amount, currency, rate = parse_compensation(description[:4000])
            yield RawPosting(
                external_id=posting_id,
                company=target.company,
                title=str(entry.get("name") or "Untitled role").strip(),
                location=_location(entry),
                apply_url=_apply_url(entry, target.board_token, posting_id),
                description=description,
                date_posted=iso_date(entry.get("releasedDate") or entry.get("createdOn")),
                source_type=self.source_type,
                amount=amount,
                currency=currency,
                rate=rate,
            )

    def _advert(self, listing: str, posting_id: str) -> str:
        """The advert text, which the list endpoint does not carry."""
        try:
            detail = self.get_json(f"{listing}/{posting_id}")
        except ConnectorError as exc:
            self.log(f"smartrecruiters: no advert for {posting_id} ({exc})", "warning")
            return ""
        sections = (
            detail.get("jobAd", {}).get("sections", {}) if isinstance(detail, dict) else {}
        )
        parts = [
            (sections.get(name) or {}).get("text", "")
            for name in (
                "companyDescription",
                "jobDescription",
                "qualifications",
                "additionalInformation",
            )
        ]
        return strip_html("\n\n".join(part for part in parts if part))


def _location(entry: dict[str, Any]) -> str:
    location = entry.get("location") or {}
    if not isinstance(location, dict):
        return str(location).strip() or "Not specified"
    parts = [location.get("city"), location.get("region"), location.get("country")]
    joined = ", ".join(str(part).strip() for part in parts if str(part or "").strip())
    if location.get("remote"):
        return f"Remote, {joined}" if joined else "Remote"
    return joined or "Not specified"


def _apply_url(entry: dict[str, Any], token: str, posting_id: str) -> str:
    applicant = entry.get("applyUrl") or entry.get("postingUrl")
    if applicant:
        return str(applicant)
    return f"https://careers.smartrecruiters.com/{token}/{posting_id}"

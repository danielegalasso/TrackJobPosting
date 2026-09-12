"""Workday CXS connector.

Endpoints, for a tenant `nxp` on host `nxp.wd3.myworkdayjobs.com` and
career site `careers`:

  POST https://nxp.wd3.myworkdayjobs.com/wday/cxs/nxp/careers/jobs
  GET  https://nxp.wd3.myworkdayjobs.com/wday/cxs/nxp/careers/<externalPath>

This is the endpoint every Workday career site loads its own listings from:
public, unauthenticated, and the reason a Workday page shows nothing to a
plain HTML fetch. It is a POST rather than a GET, which is why it is easy to
conclude no API exists.

Two traps are handled here. A `limit` above 20 returns zero results with no
error at all, so pages are requested at 20. And the list carries no advert
text, so each posting's own CXS document is fetched for it.

Board token: the careers host and site, e.g. `nxp.wd3.myworkdayjobs.com/careers`.
A full URL works too, locale segment and all.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from ..compensation import parse_compensation
from ..models import RawPosting, TargetSource
from .base import Connector, ConnectorError, iso_date, register, strip_html

#: Workday silently returns an empty page for anything larger.
PAGE_SIZE = 20
_LOCALE_RE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")
_POSTED_RE = re.compile(r"(\d+)\+?\s*Days?\s*Ago", re.IGNORECASE)


@dataclass(frozen=True)
class Board:
    """The three values a Workday CXS URL needs."""

    host: str
    tenant: str
    site: str

    @property
    def jobs_url(self) -> str:
        return f"https://{self.host}/wday/cxs/{self.tenant}/{self.site}/jobs"

    def detail_url(self, external_path: str) -> str:
        return f"https://{self.host}/wday/cxs/{self.tenant}/{self.site}{external_path}"

    @property
    def careers_url(self) -> str:
        return f"https://{self.host}/{self.site}"


def parse_board(token: str) -> Board:
    """Read host, tenant and site out of a configured token.

    Accepts `nxp.wd3.myworkdayjobs.com/careers`, a full career-site URL, or
    a locale-prefixed one such as `.../en-US/careers`.
    """
    raw = token.strip()
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    host = parsed.netloc
    segments = [segment for segment in parsed.path.split("/") if segment]
    # A locale segment sits between the host and the site name.
    segments = [segment for segment in segments if not _LOCALE_RE.match(segment)]
    if not host or not segments:
        raise ConnectorError(
            f"workday token {token!r} must look like nxp.wd3.myworkdayjobs.com/careers"
        )
    tenant = host.split(".")[0]
    return Board(host=host, tenant=tenant, site=segments[0])


@register
class WorkdayConnector(Connector):
    source_type = "workday"
    token_hint = "Careers host and site, e.g. nxp.wd3.myworkdayjobs.com/careers"

    def _post_json(self, url: str, body: dict[str, Any]) -> Any:
        self._throttle()
        try:
            response = self.client.post(url, json=body)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"{url}: {exc}") from exc
        if response.status_code == 404:
            raise ConnectorError(f"{url}: board not found (404) — check the board token")
        if response.status_code == 429:
            raise ConnectorError(f"{url}: rate limited by the source (429); backing off")
        if response.status_code >= 400:
            raise ConnectorError(f"{url}: HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise ConnectorError(f"{url}: response was not JSON") from exc

    def fetch(self, target: TargetSource) -> Iterable[RawPosting]:
        board = parse_board(target.board_token)
        postings: list[dict[str, Any]] = []
        offset = 0
        while len(postings) < self.max_jobs:
            payload = self._post_json(
                board.jobs_url,
                {
                    "appliedFacets": {},
                    "limit": PAGE_SIZE,
                    "offset": offset,
                    "searchText": "",
                },
            )
            page = payload.get("jobPostings", []) if isinstance(payload, dict) else []
            if not page:
                break
            postings.extend(item for item in page if isinstance(item, dict))
            offset += len(page)
            if len(page) < PAGE_SIZE:
                break
        self.log(f"workday/{board.tenant}: {len(postings)} postings listed")

        for entry in postings[: self.max_jobs]:
            external_path = str(entry.get("externalPath") or "")
            detail = self._detail(board, external_path)
            description = strip_html(detail.get("jobDescription") or "")
            amount, currency, rate = parse_compensation(description[:4000])
            yield RawPosting(
                external_id=_external_id(entry, external_path),
                company=target.company,
                title=str(entry.get("title") or "Untitled role").strip(),
                location=str(
                    entry.get("locationsText") or detail.get("location") or ""
                ).strip()
                or "Not specified",
                apply_url=(
                    detail.get("externalUrl")
                    or (f"https://{board.host}/{board.site}{external_path}"
                        if external_path else board.careers_url)
                ),
                description=description,
                date_posted=iso_date(detail.get("startDate") or detail.get("postedOn")),
                source_type=self.source_type,
                amount=amount,
                currency=currency,
                rate=rate,
            )

    def _detail(self, board: Board, external_path: str) -> dict[str, Any]:
        """One posting's own CXS document, which carries the advert."""
        if not external_path:
            return {}
        try:
            payload = self.get_json(board.detail_url(external_path))
        except ConnectorError as exc:
            self.log(f"workday: no advert for {external_path} ({exc})", "warning")
            return {}
        info = payload.get("jobPostingInfo") if isinstance(payload, dict) else None
        return info if isinstance(info, dict) else {}


def _external_id(entry: dict[str, Any], external_path: str) -> str:
    """Workday puts the requisition number in bulletFields."""
    bullets = entry.get("bulletFields") or []
    if isinstance(bullets, list) and bullets:
        return str(bullets[0])
    return external_path.rsplit("/", 1)[-1] if external_path else ""

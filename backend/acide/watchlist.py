"""Turn a curated list of organizations into indexable career feeds.

The input is a JSON array of records shaped like:

    {"id": 1, "organization": "NVISO", "category": "Cybersecurity",
     "website": "https://www.nviso.eu", "careers_page": "https://…/careers/"}

`careers_page` is a landing page, not a board endpoint, so each one is
fetched once and read for the ATS it is wired to. Whatever resolves to a
connector ACIDE-Watch has becomes a target; everything else is reported, with
the reason, so the list stays a worklist rather than silently shrinking.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx

from .discovery import Discovery, candidate_tokens, discover_in_html
from .models import TargetSource
from .spider.base import DEFAULT_TIMEOUT

BROWSER_UA = (
    "ACIDE-Watch/2.0 (self-hosted job alert agent; "
    "+https://github.com/danielegalasso/TrackJobPosting)"
)


@dataclass
class Organization:
    """One entry of the curated list."""

    organization: str
    category: str = ""
    website: str = ""
    careers_page: str = ""
    id: int | None = None


@dataclass
class Resolution:
    """What was learned about one organization."""

    organization: str
    category: str = ""
    website: str = ""
    careers_page: str = ""
    source_type: str | None = None
    board_token: str | None = None
    job_count: int | None = None
    other_ats: str | None = None
    status: str = "unresolved"
    detail: str = ""

    @property
    def resolved(self) -> bool:
        return self.status == "resolved"

    def as_target(self) -> TargetSource:
        if not (self.source_type and self.board_token):
            raise ValueError(f"{self.organization} did not resolve to a board")
        return TargetSource(
            company=self.organization,
            source_type=self.source_type,  # type: ignore[arg-type]
            board_token=self.board_token,
            enabled=True,
        )


@dataclass
class ImportReport:
    """The outcome of one import run."""

    resolutions: list[Resolution] = field(default_factory=list)

    @property
    def resolved(self) -> list[Resolution]:
        return [item for item in self.resolutions if item.resolved]

    @property
    def unresolved(self) -> list[Resolution]:
        return [item for item in self.resolutions if not item.resolved]

    def by_other_ats(self) -> dict[str, int]:
        """Which unsupported platforms account for the rest, and how many."""
        counts: dict[str, int] = {}
        for item in self.unresolved:
            key = item.other_ats or "unknown"
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items(), key=lambda pair: -pair[1]))

    def to_json(self) -> str:
        return json.dumps(
            {
                "resolved": [asdict(item) for item in self.resolved],
                "unresolved": [asdict(item) for item in self.unresolved],
                "summary": {
                    "total": len(self.resolutions),
                    "resolved": len(self.resolved),
                    "unresolved": len(self.unresolved),
                    "unsupported_platforms": self.by_other_ats(),
                },
            },
            indent=2,
        )


def load_organizations(path: Path | str) -> list[Organization]:
    """Read the curated JSON list, ignoring fields we do not use."""
    raw = json.loads(Path(path).read_text("utf-8"))
    if not isinstance(raw, list):
        raise ValueError("the companies file must contain a JSON array")

    organizations: list[Organization] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("organization") or entry.get("company") or "").strip()
        if not name:
            continue
        organizations.append(
            Organization(
                organization=name,
                category=str(entry.get("category") or "").strip(),
                website=str(entry.get("website") or "").strip(),
                careers_page=str(entry.get("careers_page") or "").strip(),
                id=entry.get("id") if isinstance(entry.get("id"), int) else None,
            )
        )
    return organizations


#: Endpoints that confirm a board token really exists, and count its postings.
_VERIFIERS: dict[str, Callable[[str], tuple[str, dict[str, str]]]] = {
    "greenhouse": lambda token: (
        f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
        {},
    ),
    "lever": lambda token: (f"https://api.lever.co/v0/postings/{token}", {"mode": "json"}),
    "ashby": lambda token: (
        f"https://api.ashbyhq.com/posting-api/job-board/{token}",
        {},
    ),
}


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _count_postings(source_type: str, payload: object) -> int | None:
    if source_type == "lever":
        return len(payload) if isinstance(payload, list) else None
    if isinstance(payload, dict):
        jobs = payload.get("jobs")
        return len(jobs) if isinstance(jobs, list) else None
    return None


def verify_token(client: httpx.Client, source_type: str, token: str) -> int | None:
    """Confirm a board exists, returning how many postings it has.

    `None` means the board did not answer — a guessed token that belongs to
    nobody, or a provider refusing the request.
    """
    builder = _VERIFIERS.get(source_type)
    if builder is None:
        return None
    url, params = builder(token)
    try:
        response = client.get(url, params=params)
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        return _count_postings(source_type, response.json())
    except ValueError:
        return None


def resolve_one(
    client: httpx.Client,
    org: Organization,
    *,
    guess: bool = False,
    on_log: Callable[[str], None] | None = None,
) -> Resolution:
    """Fetch one careers page and work out whether it can be indexed."""
    result = Resolution(
        organization=org.organization,
        category=org.category,
        website=org.website,
        careers_page=org.careers_page,
    )

    discovery = Discovery(note="no careers page given")
    if org.careers_page:
        try:
            response = client.get(org.careers_page)
            if response.status_code == 200:
                discovery = discover_in_html(response.text)
            else:
                discovery = Discovery(note=f"careers page returned HTTP {response.status_code}")
        except httpx.HTTPError as exc:
            discovery = Discovery(note=f"careers page unreachable: {exc}")

    if discovery.supported:
        count = verify_token(client, discovery.source_type or "", discovery.board_token or "")
        if count is not None:
            result.source_type = discovery.source_type
            result.board_token = discovery.board_token
            result.job_count = count
            result.status = "resolved"
            result.detail = f"found on the careers page · {_plural(count, 'posting')}"
            if on_log:
                board = f"{result.source_type}:{result.board_token}"
                on_log(f"  ✓ {org.organization}: {board} ({count})")
            return result
        discovery = Discovery(
            note=(
                f"page links {discovery.source_type}:{discovery.board_token}, "
                "but that board did not answer"
            )
        )

    if guess:
        for token in candidate_tokens(org.organization, org.website):
            for source_type in _VERIFIERS:
                count = verify_token(client, source_type, token)
                if count is None:
                    continue
                result.source_type = source_type
                result.board_token = token
                result.job_count = count
                result.status = "resolved"
                result.detail = f"guessed token · {count} postings"
                if on_log:
                    on_log(f"  ✓ {org.organization}: {source_type}:{token} ({count}, guessed)")
                return result

    result.other_ats = discovery.other_ats
    result.detail = discovery.note
    if on_log:
        on_log(f"  · {org.organization}: {discovery.other_ats or 'no supported ATS'}")
    return result


def resolve_all(
    organizations: Sequence[Organization],
    *,
    guess: bool = False,
    workers: int = 6,
    timeout: httpx.Timeout | None = None,
    client: httpx.Client | None = None,
    on_log: Callable[[str], None] | None = None,
) -> ImportReport:
    """Resolve a whole list, one careers-page request per organization."""
    owns_client = client is None
    client = client or httpx.Client(
        timeout=timeout or DEFAULT_TIMEOUT,
        headers={"User-Agent": BROWSER_UA, "Accept": "text/html,application/json"},
        follow_redirects=True,
    )
    try:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            resolutions = list(
                pool.map(
                    lambda org: resolve_one(client, org, guess=guess, on_log=on_log),
                    organizations,
                )
            )
    finally:
        if owns_client:
            client.close()
    return ImportReport(resolutions=resolutions)


def resolve_all_with_browser(
    organizations: Sequence[Organization],
    *,
    cdp_url: str | None = None,
    headless: bool = True,
    settle_ms: int = 2500,
    delay_seconds: float = 2.0,
    executable_path: str | None = None,
    user_data_dir: str | None = None,
    client: httpx.Client | None = None,
    on_log: Callable[[str], None] | None = None,
    on_resolution: Callable[[Resolution], None] | None = None,
) -> ImportReport:
    """Resolve through a real browser, for pages that only exist after JS.

    The browser finds the board; the board's own JSON API still confirms it,
    exactly as in the plain-HTTP path — a rendered link is evidence, not
    proof.

    Each organization is verified as its page finishes rather than in a
    second pass at the end, and `on_resolution` is called with the outcome,
    so a caller can checkpoint. If this raises part-way — a browser crash, a
    killed process — everything handed to `on_resolution` is still good.
    """
    from .browser_discovery import BrowserSession, resolve_with_browser

    owns_client = client is None
    client = client or httpx.Client(
        timeout=DEFAULT_TIMEOUT,
        headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
        follow_redirects=True,
    )
    resolutions: list[Resolution] = []

    def record(org: Organization, page_result: object) -> None:
        resolution = Resolution(
            organization=org.organization,
            category=org.category,
            website=org.website,
            careers_page=org.careers_page,
        )
        found = page_result.discovery  # type: ignore[attr-defined]
        if found.supported:
            count = verify_token(client, found.source_type or "", found.board_token or "")
            if count is not None:
                resolution.source_type = found.source_type
                resolution.board_token = found.board_token
                resolution.job_count = count
                resolution.status = "resolved"
                resolution.detail = f"found in the browser · {_plural(count, 'posting')}"
            else:
                resolution.detail = (
                    f"page uses {found.source_type}:{found.board_token}, "
                    "but that board did not answer"
                )
        else:
            resolution.other_ats = found.other_ats
            resolution.detail = found.note
        resolutions.append(resolution)
        if on_resolution:
            on_resolution(resolution)

    try:
        with BrowserSession(
            cdp_url=cdp_url,
            headless=headless,
            settle_ms=settle_ms,
            executable_path=executable_path,
            user_data_dir=user_data_dir,
        ) as session:
            resolve_with_browser(
                session,
                organizations,
                on_log=on_log,
                on_result=record,
                delay_seconds=delay_seconds,
            )
        return ImportReport(resolutions=resolutions)
    finally:
        if owns_client:
            client.close()


def organizations_from_report(path: Path | str) -> list[Organization]:
    """The unresolved entries of an earlier report, to retry.

    Re-running a whole list through a browser to revisit the few hundred
    that failed would be wasteful and impolite.
    """
    payload = json.loads(Path(path).read_text("utf-8"))
    entries = payload.get("unresolved", []) if isinstance(payload, dict) else []
    return [
        Organization(
            organization=entry.get("organization", ""),
            category=entry.get("category", ""),
            website=entry.get("website", ""),
            careers_page=entry.get("careers_page", ""),
        )
        for entry in entries
        if entry.get("organization")
    ]


def merge_targets(
    existing: Iterable[TargetSource], resolved: Iterable[Resolution]
) -> list[TargetSource]:
    """Add resolved boards to the configured targets, without duplicates.

    An existing target wins: the operator may have corrected a token or
    disabled a company deliberately, and an import should not undo that.
    """
    merged = list(existing)
    seen = {(target.source_type, target.board_token.lower()) for target in merged}
    for item in resolved:
        target = item.as_target()
        key = (target.source_type, target.board_token.lower())
        if key in seen:
            continue
        seen.add(key)
        merged.append(target)
    return merged

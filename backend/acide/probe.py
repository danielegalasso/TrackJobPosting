"""Ask what an unresolved careers page actually contains, over plain HTTP.

Browser mode takes over an hour on six hundred pages. Before spending that, it
is worth knowing what is there — and most of the question can be answered
without a browser at all, in a couple of minutes:

- does the page link to a board that is now recognised (Breezy, BambooHR,
  Pinpoint and the rest arrived after earlier runs);
- does it publish its own postings as schema.org JobPosting;
- or does it genuinely render everything with JavaScript, which is the only
  case where a browser is the answer.

This is a dry run of discovery, not an import: nothing is written to setup.json
and no board is contacted.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field

import httpx

from .discovery import discover_in_html, has_job_posting_markup

#: Browser-shaped but identified, exactly as the link checker asks.
PROBE_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0 Safari/537.36 ACIDE-Watch/2.0 (+careers page probe)"
)


@dataclass
class Probe:
    """What one careers page turned out to be carrying."""

    organization: str
    careers_page: str
    verdict: str = "nothing found"
    source_type: str | None = None
    board_token: str | None = None
    other_ats: str | None = None
    status: int | None = None
    has_structured_postings: bool = False
    note: str = ""

    @property
    def resolvable(self) -> bool:
        return bool(self.source_type and self.board_token)


@dataclass
class ProbeReport:
    probes: list[Probe] = field(default_factory=list)

    def by_verdict(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for probe in self.probes:
            counts[probe.verdict] = counts.get(probe.verdict, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    def by_source_type(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for probe in self.probes:
            if probe.source_type:
                counts[probe.source_type] = counts.get(probe.source_type, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    def summary_line(self) -> str:
        resolvable = sum(1 for probe in self.probes if probe.resolvable)
        structured = sum(
            1 for probe in self.probes if probe.has_structured_postings
        )
        refusals = sum(1 for probe in self.probes if probe.verdict == "refuses plain HTTP")
        line = (
            f"{resolvable} of {len(self.probes)} would resolve now "
            f"({structured} publish their own structured postings)"
        )
        if refusals:
            line += f"; {refusals} refused this plain request but load in a browser"
        return line

    def to_json(self) -> str:
        return json.dumps(
            {
                "probes": [asdict(probe) for probe in self.probes],
                "summary": {
                    "total": len(self.probes),
                    "would_resolve_now": sum(1 for p in self.probes if p.resolvable),
                    "by_verdict": self.by_verdict(),
                    "by_source_type": self.by_source_type(),
                },
            },
            indent=2,
        )


#: Phrases in an earlier run's detail meaning the browser did fetch the page.
_BROWSER_SAW_THE_PAGE = ("no ATS link found", "runs on ", "did not answer")


def _browser_loaded_it(previous_detail: str) -> bool:
    return any(mark in previous_detail for mark in _BROWSER_SAW_THE_PAGE)


def probe_one(
    client: httpx.Client,
    organization: str,
    careers_page: str,
    previous_detail: str = "",
) -> Probe:
    """Fetch one careers page and say what discovery would now make of it."""
    result = Probe(organization=organization, careers_page=careers_page)
    if not careers_page:
        result.note = "no careers page given"
        return result

    try:
        response = client.get(careers_page, headers={"Accept": "text/html"})
    except httpx.HTTPError as exc:
        result.verdict = (
            "refuses plain HTTP" if _browser_loaded_it(previous_detail) else "unreachable"
        )
        result.note = str(exc)[:160]
        return result

    result.status = response.status_code
    if response.status_code >= 400:
        # A 404 or 403 here is not proof the page is gone. Plenty of sites
        # answer anything that is not a real browser with one — some with 404
        # rather than 403, which reads as "deleted" and is not. When an
        # earlier browser run did fetch this page, say which it was.
        if _browser_loaded_it(previous_detail):
            result.verdict = "refuses plain HTTP"
            result.note = (
                f"HTTP {response.status_code} to a plain request, but a browser "
                "loaded this page — the page is there, the request was refused"
            )
        else:
            result.verdict = f"HTTP {response.status_code}"
        return result

    html = response.text
    result.has_structured_postings = has_job_posting_markup(html)
    found = discover_in_html(html, str(response.url))

    if found.supported:
        result.source_type = found.source_type
        result.board_token = found.board_token
        result.verdict = (
            "publishes its own postings"
            if found.source_type == "jsonld"
            else "links a board we can read"
        )
        return result

    if found.other_ats:
        result.other_ats = found.other_ats
        result.verdict = f"runs on {found.other_ats}"
        return result

    # Nothing in the served HTML. Either the list is built by JavaScript, or
    # the page really has no postings on it.
    result.verdict = "needs a browser"
    result.note = found.note
    return result


def probe_all(
    organizations: Sequence[object],
    *,
    client: httpx.Client | None = None,
    workers: int = 8,
    on_log: Callable[[str], None] | None = None,
) -> ProbeReport:
    """Probe a whole unresolved list concurrently."""
    owns_client = client is None
    client = client or httpx.Client(
        timeout=httpx.Timeout(20.0, connect=10.0),
        follow_redirects=True,
        headers={"User-Agent": PROBE_UA, "Accept": "text/html"},
    )

    def run(org: object) -> Probe:
        outcome = probe_one(
            client,
            getattr(org, "organization", "?"),
            getattr(org, "careers_page", "") or "",
            getattr(org, "previous_detail", "") or "",
        )
        if on_log:
            if outcome.resolvable:
                on_log(f"  ✓ {outcome.organization}: {outcome.source_type}:{outcome.board_token}")
            else:
                on_log(f"  · {outcome.organization}: {outcome.verdict}")
        return outcome

    try:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            return ProbeReport(probes=list(pool.map(run, organizations)))
    finally:
        if owns_client:
            client.close()

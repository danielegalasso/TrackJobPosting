"""Work out which ATS a company's careers page is built on.

A careers page URL is not a board endpoint: the connectors need a board
token, and nothing in `https://www.nviso.eu/careers/` reveals one. But a
careers page almost always *embeds* or links to its ATS, so fetching it once
and reading the links out is both accurate and cheap — one request per
company, rather than guessing tokens against three providers.

Guessing is available as a fallback (`candidate_tokens`), but it is opt-in:
it turns one request per company into several, nearly all of them 404s
against somebody else's API.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlparse

#: Patterns that pin a board token inside a careers page's markup.
#: Ordered: the more specific embed forms first, so a page carrying both an
#: embed and a plain link resolves from the embed.
_ATS_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # boards.greenhouse.io/embed/job_board?for=token
    ("greenhouse", re.compile(r"greenhouse\.io/embed/job_board[^\"'\s]*?[?&]for=([A-Za-z0-9_-]+)")),
    # job-boards.greenhouse.io/token, boards.greenhouse.io/token
    ("greenhouse", re.compile(r"(?:job-)?boards\.greenhouse\.io/(?:embed/)?([A-Za-z0-9_-]+)")),
    ("greenhouse", re.compile(r"boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)")),
    # jobs.lever.co/handle
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([A-Za-z0-9_-]+)")),
    ("lever", re.compile(r"api\.lever\.co/v0/postings/([A-Za-z0-9_-]+)")),
    # jobs.ashbyhq.com/name
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.-]+)")),
    ("ashby", re.compile(r"api\.ashbyhq\.com/posting-api/job-board/([A-Za-z0-9_.-]+)")),
    # Workday needs host *and* site to address its CXS endpoint, so the token
    # is both: nxp.wd3.myworkdayjobs.com/careers. A locale segment may sit
    # between them (…/en-US/careers) and is skipped.
    (
        "workday",
        re.compile(
            r"([a-z0-9-]+\.wd\d+\.myworkdayjobs\.com)"
            r"(?:/[a-z]{2}-[A-Z]{2})?/([A-Za-z0-9_-]+)"
        ),
    ),
    # <name>.teamtailor.com
    ("teamtailor", re.compile(r"([A-Za-z0-9-]+)\.teamtailor\.com")),
    # <name>.jobs.personio.de / .com
    ("personio", re.compile(r"([A-Za-z0-9-]+)\.jobs\.personio\.(?:de|com)")),
    # <name>.recruitee.com
    ("recruitee", re.compile(r"([A-Za-z0-9-]+)\.recruitee\.com")),
    # apply.workable.com/<slug>, and the legacy <slug>.workable.com
    ("workable", re.compile(r"apply\.workable\.com/(?:j/)?([A-Za-z0-9_-]+)")),
    ("workable", re.compile(r"([A-Za-z0-9-]+)\.workable\.com")),
    # careers.smartrecruiters.com/<Company>, jobs.smartrecruiters.com/<Company>
    (
        "smartrecruiters",
        re.compile(r"(?:careers|jobs)\.smartrecruiters\.com/([A-Za-z0-9_-]+)"),
    ),
    (
        "smartrecruiters",
        re.compile(r"api\.smartrecruiters\.com/v1/companies/([A-Za-z0-9_-]+)"),
    ),
)

#: Tokens the patterns can match that are never a real board.
_NOT_TOKENS = frozenset(
    {
        "embed",
        "job_board",
        "jobs",
        "www",
        "api",
        "v1",
        "boards",
        "static",
        "assets",
        "js",
        "css",
        "images",
        "favicon.ico",
    }
)

#: Other ATS platforms seen on these careers pages. Recognising them does not
#: make them indexable — it explains *why* a company cannot be watched yet,
#: which is more useful than "unresolved".
#: Platforms still without a connector. The ones that have gained one —
#: Workday, Teamtailor, Personio, Recruitee, Workable, SmartRecruiters — are
#: matched by _ATS_PATTERNS above and no longer belong here; a page that
#: names one but yields no token falls through to these as a last resort, so
#: "runs on workday" still beats "no ATS link found".
_KNOWN_OTHER_ATS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("successfactors", re.compile(r"successfactors\.(?:eu|com)|sapsf\.(?:eu|com)")),
    ("taleo", re.compile(r"taleo\.net")),
    ("icims", re.compile(r"icims\.com")),
    ("eu-careers", re.compile(r"eu-careers\.europa\.eu|epso\.europa\.eu")),
    ("oracle-cloud", re.compile(r"oraclecloud\.com/hcmUI|fa\.em\d+\.oraclecloud\.com")),
    ("workday", re.compile(r"myworkdayjobs\.com|workday\.com")),
    ("smartrecruiters", re.compile(r"smartrecruiters\.com")),
    ("workable", re.compile(r"workable\.com")),
    ("personio", re.compile(r"personio\.(?:de|com)")),
    ("teamtailor", re.compile(r"teamtailor\.com")),
    ("recruitee", re.compile(r"recruitee\.com")),
)


class Discovery:
    """What one careers page turned out to be running."""

    __slots__ = ("source_type", "board_token", "other_ats", "note")

    def __init__(
        self,
        source_type: str | None = None,
        board_token: str | None = None,
        other_ats: str | None = None,
        note: str = "",
    ) -> None:
        self.source_type = source_type
        self.board_token = board_token
        self.other_ats = other_ats
        self.note = note

    @property
    def supported(self) -> bool:
        """True when ACIDE-Watch has a connector for what was found."""
        return bool(self.source_type and self.board_token)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        if self.supported:
            return f"<Discovery {self.source_type}:{self.board_token}>"
        return f"<Discovery unsupported={self.other_ats or 'unknown'}>"


def discover_in_html(html: str) -> Discovery:
    """Read the ATS a careers page is wired to out of its markup."""
    if not html:
        return Discovery(note="empty page")

    for source_type, pattern in _ATS_PATTERNS:
        for match in pattern.finditer(html):
            groups = [part.strip().strip("/") for part in match.groups() if part]
            if not groups or any(part.lower() in _NOT_TOKENS for part in groups):
                continue
            # Workday alone needs two: its endpoint is addressed by host *and*
            # career-site name, so the token carries both.
            token = "/".join(groups)
            if token:
                return Discovery(source_type=source_type, board_token=token)

    for name, pattern in _KNOWN_OTHER_ATS:
        if pattern.search(html):
            return Discovery(other_ats=name, note=f"runs on {name}, which has no connector yet")

    return Discovery(note="no ATS link found on the page")


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _drop_parentheticals(name: str) -> str:
    """"Fox-IT (NCC Group)" is really two companies; keep the first."""
    return re.sub(r"\s*[\(\[].*?[\)\]]", "", name).strip()


#: Corporate suffixes that are never part of a board token.
_SUFFIXES = (
    "s.p.a.",
    "spa",
    "gmbh",
    "ag",
    "sa",
    "nv",
    "bv",
    "ltd",
    "limited",
    "inc",
    "plc",
    "group",
    "holding",
    "technologies",
    "technology",
    "systems",
    "solutions",
    "international",
    "industries",
)


def candidate_tokens(organization: str, website: str = "") -> list[str]:
    """Plausible board tokens for a company, best guess first.

    Only used with `--guess`, when the careers page gave nothing away. The
    domain is the strongest signal, so it leads.
    """
    candidates: list[str] = []

    def add(value: str) -> None:
        value = value.strip("-. ")
        if value and value not in candidates and len(value) > 1:
            candidates.append(value)

    host = urlparse(website).netloc.lower() if website else ""
    host = re.sub(r"^(www|careers|jobs|job-boards)\.", "", host)
    if host:
        label = host.split(".")[0]
        # "protect.airbus.com" — the registrable name beats the subdomain.
        parts = host.split(".")
        if len(parts) >= 3 and parts[0] not in {"co", "com"}:
            add(parts[1])
        add(label)

    name = _strip_accents(_drop_parentheticals(organization)).lower()
    name = name.replace("&", " and ")
    words = [word for word in re.split(r"[^a-z0-9]+", name) if word]
    meaningful = [word for word in words if word not in _SUFFIXES] or words

    if meaningful:
        add("".join(meaningful))
        add("-".join(meaningful))
        add(meaningful[0])
        if len(meaningful) >= 2:
            add("".join(meaningful[:2]))
    return candidates

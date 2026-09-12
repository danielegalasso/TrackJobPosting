"""Check curated careers URLs, and find the right one when a link has rotted.

A hand-researched list decays: paths move, companies rebrand, domains lapse.
Roughly a quarter of a real 631-entry list answers 404 or fails to resolve.

Repair goes in order of how much it can be trusted:

1. **Follow redirects.** A 301 to the new path is the site telling us where
   the page went; take it at its word.
2. **Read the site's own navigation.** Fetch the homepage and look for the
   link a visitor would click. This is the reliable route, and it has to be
   multilingual — this kind of list is full of `lavora-con-noi`, `karriere`,
   `carrieres` and `vacatures`.
3. **Try conventional paths.** `/careers`, `/jobs` and friends, last,
   because it is guessing.

Every candidate is fetched and checked before it is proposed, so nothing is
suggested that has not been seen to work.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx

#: Words that mark a careers link, across the languages this kind of list
#: spans. Matched against both link text and href.
CAREERS_WORDS: tuple[str, ...] = (
    "career",
    "careers",
    "job",
    "jobs",
    "vacancy",
    "vacancies",
    "vacature",          # nl
    "vacatures",         # nl
    "werken-bij",        # nl
    "lavora-con-noi",    # it
    "lavoraconnoi",      # it
    "carriere",          # it / fr
    "carrieres",         # fr
    "carrière",          # fr
    "emploi",            # fr
    "rejoignez",         # fr
    "karriere",          # de
    "karriera",          # de/pl
    "stellenangebote",   # de
    "kariera",           # pl
    "empleo",            # es
    "talento",           # es
    "talent",
    "trabaja-con-nosotros",  # es
    "join-us",
    "joinus",
    "work-with-us",
    "working-at",
    "recruitment",
    "opportunities",
    "openings",
)

#: Conventional paths, tried only after the site's own navigation.
FALLBACK_PATHS: tuple[str, ...] = (
    "/careers",
    "/careers/",
    "/en/careers",
    "/jobs",
    "/jobs/",
    "/company/careers",
    "/about/careers",
    "/en/jobs",
    "/join-us",
    "/work-with-us",
    "/vacancies",
    "/karriere",
    "/lavora-con-noi",
)

_LINK_RE = re.compile(
    r"""<a\b[^>]*?href\s*=\s*["']([^"'#]+)["'][^>]*>(.*?)</a>""",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class UrlCheck:
    """What one URL turned out to be."""

    url: str
    status: int | None = None
    final_url: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status is not None and 200 <= self.status < 400

    @property
    def redirected(self) -> bool:
        if not (self.ok and self.final_url):
            return False
        return self.final_url.rstrip("/") != self.url.rstrip("/")


@dataclass
class Repair:
    """The verdict on one organization's careers URL."""

    organization: str
    category: str = ""
    website: str = ""
    original: str = ""
    verdict: str = "working"
    status: int | None = None
    suggested: str = ""
    how: str = ""
    note: str = ""
    tried: list[str] = field(default_factory=list)

    @property
    def needs_attention(self) -> bool:
        return self.verdict != "working"


def careers_links(html: str, base_url: str) -> list[str]:
    """Careers-looking links from a page, most promising first.

    Reading the site's own navigation beats guessing paths: it is what a
    visitor would click, and it works whatever the site calls the page.
    """
    if not html:
        return []
    host = urlparse(base_url).netloc.lower()
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()

    for href, inner in _LINK_RE.findall(html):
        href = href.strip()
        if not href or href.lower().startswith(("mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https"):
            continue
        if absolute in seen:
            continue

        text = _TAG_RE.sub(" ", inner).strip().lower()
        path = parsed.path.lower()
        haystack_path = path.strip("/").replace("_", "-")
        words_in_text = any(word in text for word in CAREERS_WORDS)
        words_in_path = any(word in haystack_path for word in CAREERS_WORDS)
        if not (words_in_text or words_in_path):
            continue

        score = 0
        if words_in_path:
            score += 3
            # A short path is the careers landing page; a long one is one job.
            score += 2 if haystack_path.count("/") == 0 else 0
        if words_in_text:
            score += 2
        # Prefer staying on the company's own domain.
        if parsed.netloc.lower() == host:
            score += 2
        elif host and host.split(".")[-2:] == parsed.netloc.lower().split(".")[-2:]:
            score += 1
        seen.add(absolute)
        scored.append((score, absolute))

    scored.sort(key=lambda pair: (-pair[0], len(pair[1])))
    return [url for _, url in scored]


def looks_like_careers_url(url: str) -> bool:
    """Does this URL claim to be a careers page, by host or path?

    Used to judge a redirect. `careers.thalesgroup.com/global/en` carries the
    signal in its host and `nviso.eu/jobs` in its path; `acme.com/` carries
    none, and a site root is never a careers page.
    """
    parsed = urlparse(url)
    haystack = f"{parsed.netloc}{parsed.path}".lower().replace("_", "-")
    return any(word in haystack for word in CAREERS_WORDS)


def _give_up(result: Repair, redirect_fallback: str) -> Repair:
    """Nothing better was found; keep the redirect destination if there is one.

    It is a worse answer than a real careers page but a better one than
    nothing: the site did send us there, and it does respond.
    """
    if redirect_fallback:
        result.verdict = "moved"
        result.suggested = redirect_fallback
        result.how = "followed a redirect; no careers link found there"
    else:
        result.verdict = "broken"
    return result


def check_url(client: httpx.Client, url: str) -> UrlCheck:
    """Fetch one URL and report what happened."""
    try:
        response = client.get(url)
    except httpx.HTTPError as exc:
        return UrlCheck(url=url, error=str(exc)[:160])
    return UrlCheck(url=url, status=response.status_code, final_url=str(response.url))


def _root(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"https://{url}")
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else ""


def repair_one(
    client: httpx.Client,
    *,
    organization: str,
    careers_page: str,
    website: str = "",
    category: str = "",
    max_candidates: int = 6,
) -> Repair:
    """Check one careers URL and, if it is dead, find where it went."""
    result = Repair(
        organization=organization,
        category=category,
        website=website,
        original=careers_page,
    )
    #: Where a redirect landed, when it landed somewhere that is not a
    #: careers page. Held back as a last resort rather than proposed.
    redirect_fallback = ""

    if careers_page:
        first = check_url(client, careers_page)
        result.status = first.status
        result.tried.append(careers_page)
        if first.ok and not first.redirected:
            result.note = f"HTTP {first.status}"
            return result
        if first.ok and looks_like_careers_url(first.final_url):
            result.verdict = "moved"
            result.suggested = first.final_url
            result.how = "followed a redirect"
            result.note = f"HTTP {first.status}"
            return result
        if first.ok:
            # Redirected somewhere carrying no careers signal at all. The
            # usual cause is a retired path pointed at the homepage, and a
            # homepage is not a careers page — taking it at face value would
            # replace a merely stale URL with a definitely wrong one. Repair
            # from the site instead, and keep this only if that finds nothing.
            redirect_fallback = first.final_url
            result.note = f"HTTP {first.status}, redirected to {first.final_url}"
        else:
            result.note = first.error or f"HTTP {first.status}"
    else:
        result.note = "no careers page given"

    # The listed URL is dead, or led somewhere unconvincing. Ask the site
    # itself where careers lives.
    root = _root(website) or _root(careers_page)
    if not root:
        return _give_up(result, redirect_fallback)

    home = check_url(client, root)
    result.tried.append(root)
    if not home.ok:
        result.note = f"{result.note}; homepage also unreachable ({home.error or home.status})"
        return _give_up(result, redirect_fallback)

    candidates: list[str] = []
    try:
        candidates = careers_links(client.get(root).text, home.final_url or root)[:max_candidates]
    except httpx.HTTPError:
        candidates = []
    how = "found in the site's own navigation"

    if not candidates:
        candidates = [urljoin(root, path) for path in FALLBACK_PATHS]
        how = "guessed a conventional path"

    for candidate in candidates:
        if candidate.rstrip("/") == careers_page.rstrip("/"):
            continue
        result.tried.append(candidate)
        check = check_url(client, candidate)
        if check.ok:
            result.verdict = "repaired"
            result.suggested = check.final_url or candidate
            result.how = how
            result.note = f"{result.note} \u2192 {check.status}"
            return result

    result.note = f"{result.note}; no working careers page found"
    return _give_up(result, redirect_fallback)


def repair_all(
    organizations: Sequence[object],
    *,
    client: httpx.Client | None = None,
    workers: int = 6,
    on_log: Callable[[str], None] | None = None,
) -> list[Repair]:
    """Check and repair a whole list."""
    from concurrent.futures import ThreadPoolExecutor

    owns_client = client is None
    client = client or httpx.Client(
        timeout=httpx.Timeout(20.0, connect=10.0),
        follow_redirects=True,
        headers={
            # Identified, but browser-shaped: many sites refuse an unfamiliar
            # agent outright, and a refusal here is a false "broken".
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0 Safari/537.36 ACIDE-Watch/2.0 (+link checker)"
            ),
            "Accept": "text/html,application/xhtml+xml",
        },
    )

    def run(org: object) -> Repair:
        outcome = repair_one(
            client,
            organization=getattr(org, "organization", "?"),
            careers_page=getattr(org, "careers_page", "") or "",
            website=getattr(org, "website", "") or "",
            category=getattr(org, "category", "") or "",
        )
        if on_log:
            if outcome.verdict == "working":
                on_log(f"  ok       {outcome.organization}")
            elif outcome.suggested:
                on_log(f"  {outcome.verdict:8} {outcome.organization} → {outcome.suggested}")
            else:
                on_log(f"  BROKEN   {outcome.organization}: {outcome.note}")
        return outcome

    try:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            return list(pool.map(run, organizations))
    finally:
        if owns_client:
            client.close()

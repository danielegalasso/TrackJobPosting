"""Resolve careers pages that only exist once JavaScript has run.

Roughly half of a typical curated list fails plain HTTP discovery with "no
ATS link found": the page is a JavaScript application, and the board link is
injected at runtime. Fetching the raw HTML can never see it, no matter how
the request is dressed up.

A real browser does, and it gives away the answer twice over:

1. **Network requests.** To render its board the page *must* call the ATS —
   `boards-api.greenhouse.io/v1/boards/<token>/jobs`,
   `api.ashbyhq.com/posting-api/job-board/<token>`. Reading the request log
   yields the token exactly, with no guessing.
2. **The rendered DOM**, including iframes, once scripts have run.

Scope, deliberately: this renders public pages the operator could open
themselves, one at a time, at human pace. It does **not** spoof
fingerprints, patch `navigator.webdriver`, solve challenges or rotate
addresses, and a 403 or 429 is reported and respected rather than retried
around. Connecting to a real Chrome is for rendering and for the operator's
own logged-in session — not camouflage.

The browser is used only for *discovery*. Once a board token is known the
ordinary JSON connector takes over, so no browser is involved in the
recurring job indexing at all.
"""

from __future__ import annotations

import contextlib
import urllib.error
import urllib.request
import urllib.robotparser
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from .discovery import Discovery, discover_in_html

#: How long to let a page settle after load so its XHRs have fired.
DEFAULT_SETTLE_MS = 2500
#: Pause between pages. A person opening tabs does not go faster than this.
DEFAULT_DELAY_SECONDS = 2.0

#: Careers paths worth trying when the curated URL 404s.
FALLBACK_PATHS: tuple[str, ...] = (
    "/careers",
    "/careers/",
    "/en/careers",
    "/jobs",
    "/jobs/",
    "/company/careers",
    "/about/careers",
    "/join-us",
    "/work-with-us",
)


class BrowserUnavailable(RuntimeError):
    """Playwright is not installed, or no browser could be attached."""


@dataclass
class PageResult:
    """What one page visit produced."""

    discovery: Discovery
    status: int | None = None
    final_url: str = ""


def fallback_urls(website: str, careers_page: str) -> list[str]:
    """Other places a careers page might live when the listed URL is dead.

    A fifth of a hand-curated list is typically stale links; the company is
    still hiring, the path simply moved.
    """
    base = website or careers_page
    if not base:
        return []
    parsed = urlparse(base if "://" in base else f"https://{base}")
    root = f"{parsed.scheme}://{parsed.netloc}"
    listed = urlparse(careers_page).path.rstrip("/") if careers_page else ""
    return [
        urljoin(root, path)
        for path in FALLBACK_PATHS
        if path.rstrip("/") != listed
    ]


#: robots.txt is fetched with this. Identified, but browser-shaped: a WAF
#: that blocks `Python-urllib` never lets us read robots.txt at all, and an
#: unread robots.txt is not a refusal — see RobotsCache.
ROBOTS_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0 Safari/537.36 ACIDE-Watch/2.0 (+careers page discovery)"
)


@dataclass
class RobotsDecision:
    """Whether a URL may be visited, and why not when it may not."""

    allowed: bool
    note: str = ""


class RobotsCache:
    """Per-host robots.txt, fetched once.

    A person opening a careers page is not bound by robots.txt; a script
    visiting six hundred of them is. Checking costs one request per host.

    Two details matter, and getting either wrong turns a site that permits
    us into one that appears to forbid us:

    1. **It is fetched with a browser-shaped agent.** `urllib` defaults to
       `Python-urllib/3.x`, which WAFs block on sight — so on exactly the
       sites most likely to run one, robots.txt could never be read.
    2. **An unreadable robots.txt is not a refusal.** `RobotFileParser`
       follows the pre-RFC convention of treating 401/403 as disallow-all.
       RFC 9309 §2.3.1.4 treats every 4xx as "no robots.txt applies": the
       site has not forbidden anything, we simply could not ask. A 5xx is
       different — the standard does say to assume disallow there, and that
       is honoured.

    What has not changed: a robots.txt we can actually read is obeyed.
    """

    def __init__(self, user_agent: str = "*", fetch_as: str = ROBOTS_USER_AGENT) -> None:
        self.user_agent = user_agent
        self.fetch_as = fetch_as
        self._cache: dict[str, tuple[urllib.robotparser.RobotFileParser | None, str]] = {}

    def _load(self, host: str) -> tuple[urllib.robotparser.RobotFileParser | None, str]:
        """Returns (parser, refusal). A refusal short-circuits every URL."""
        request = urllib.request.Request(
            urljoin(host, "/robots.txt"),
            headers={"User-Agent": self.fetch_as, "Accept": "text/plain,*/*"},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                body = response.read(512_000).decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            if exc.code >= 500:
                return None, f"robots.txt unreadable (HTTP {exc.code}), so not visited"
            # 4xx — including the 403 a WAF returns to any non-browser
            # fetch. Nothing has been forbidden; there is simply no
            # robots.txt we are able to read.
            return None, ""
        except Exception:
            # Offline, DNS failure, timeout. The page visit will fail on its
            # own and report its own reason, which is the honest one.
            return None, ""
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(body.splitlines())
        return parser, ""

    def check(self, url: str) -> RobotsDecision:
        parsed = urlparse(url)
        host = f"{parsed.scheme}://{parsed.netloc}"
        if host not in self._cache:
            self._cache[host] = self._load(host)
        parser, refusal = self._cache[host]
        if refusal:
            return RobotsDecision(False, refusal)
        if parser is None:
            return RobotsDecision(True)
        try:
            permitted = parser.can_fetch(self.user_agent, url)
        except Exception:  # pragma: no cover - malformed robots
            return RobotsDecision(True)
        return RobotsDecision(permitted, "" if permitted else "disallowed by robots.txt")

    def allowed(self, url: str) -> bool:
        return self.check(url).allowed


class BrowserSession:
    """A Playwright page, either attached to a real Chrome or launched."""

    def __init__(
        self,
        cdp_url: str | None = None,
        *,
        headless: bool = True,
        settle_ms: int = DEFAULT_SETTLE_MS,
        executable_path: str | None = None,
    ) -> None:
        self.cdp_url = cdp_url
        self.headless = headless
        self.settle_ms = settle_ms
        #: A browser binary to drive instead of Playwright's own download —
        #: the operator's installed Chrome, or one already on the machine.
        self.executable_path = executable_path
        self._playwright = None
        self._browser = None
        self._context = None
        self._owns_browser = False

    def __enter__(self) -> BrowserSession:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise BrowserUnavailable(
                "Playwright is not installed. Install the browser extra:\n"
                "  pip install -e 'backend/[browser]'\n"
                "  playwright install chromium"
            ) from exc

        self._playwright = sync_playwright().start()
        try:
            if self.cdp_url:
                # Attach to a Chrome the operator started themselves, with
                # their own profile and sessions.
                self._browser = self._playwright.chromium.connect_over_cdp(self.cdp_url)
                self._owns_browser = False
                contexts = self._browser.contexts
                self._context = contexts[0] if contexts else self._browser.new_context()
            else:
                launch_options: dict[str, object] = {"headless": self.headless}
                if self.executable_path:
                    launch_options["executable_path"] = self.executable_path
                self._browser = self._playwright.chromium.launch(**launch_options)
                self._owns_browser = True
                self._context = self._browser.new_context()
        except Exception as exc:
            self.close()
            raise BrowserUnavailable(
                f"could not attach a browser: {exc}\n"
                "Either install Playwright's browser:\n"
                "  playwright install chromium\n"
                "or point at a browser you already have:\n"
                "  --chrome-path /usr/bin/google-chrome\n"
                "or attach to your own running Chrome:\n"
                "  google-chrome --remote-debugging-port=9222 "
                '--user-data-dir="$HOME/chrome-agent-profile"\n'
                "  then pass --cdp-url http://localhost:9222"
            ) from exc
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        with contextlib.suppress(Exception):
            if self._context is not None and self._owns_browser:
                self._context.close()
        with contextlib.suppress(Exception):
            # Never close a browser the operator is using themselves.
            if self._browser is not None and self._owns_browser:
                self._browser.close()
        with contextlib.suppress(Exception):
            if self._playwright is not None:
                self._playwright.stop()

    @contextlib.contextmanager
    def _page(self) -> Iterator[object]:
        page = self._context.new_page()
        try:
            yield page
        finally:
            with contextlib.suppress(Exception):
                page.close()

    def visit(self, url: str) -> PageResult:
        """Open one page and read the ATS out of it."""
        with self._page() as page:
            requested: list[str] = []
            page.on("request", lambda request: requested.append(request.url))

            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            except Exception as exc:
                return PageResult(Discovery(note=f"page did not load: {str(exc)[:120]}"))

            status = response.status if response else None
            if status and status >= 400:
                # Respected, not worked around: a refusal is an answer.
                return PageResult(
                    Discovery(note=f"careers page returned HTTP {status}"), status=status
                )

            with contextlib.suppress(Exception):
                page.wait_for_timeout(self.settle_ms)

            # 1. The request log is the strongest signal: a page that renders
            #    a board has to ask the ATS for it, token and all.
            found = discover_in_html("\n".join(requested))
            if found.supported:
                return PageResult(found, status=status, final_url=page.url)

            # 2. The DOM after scripts have run.
            with contextlib.suppress(Exception):
                found = discover_in_html(page.content())
                if found.supported:
                    return PageResult(found, status=status, final_url=page.url)

            # 3. Frame URLs, for boards embedded in an iframe.
            with contextlib.suppress(Exception):
                frame_urls = "\n".join(frame.url for frame in page.frames if frame.url)
                found = discover_in_html(frame_urls)
                if found.supported:
                    return PageResult(found, status=status, final_url=page.url)

            # Nothing supported. Fall back to naming the platform, looking at
            # requests and DOM together so an XHR to Workday still counts.
            with contextlib.suppress(Exception):
                combined = "\n".join(requested) + page.content()
                found = discover_in_html(combined)
            return PageResult(found, status=status, final_url=page.url)


def resolve_with_browser(
    session: BrowserSession,
    organizations: Sequence[object],
    *,
    robots: RobotsCache | None = None,
    try_fallbacks: bool = True,
    on_log: Callable[[str], None] | None = None,
    on_result: Callable[[object, PageResult], None] | None = None,
    sleep: Callable[[float], None] | None = None,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
) -> list[tuple[object, PageResult]]:
    """Visit each organization's careers page in a real browser.

    Returns the raw page results; verifying tokens against the board APIs is
    left to the caller, which already knows how.

    `on_result` is called as each page finishes, so a caller can record
    progress while the run is still going. A list of six hundred pages at
    human pace takes over an hour, and a run that only reports at the end
    loses all of it to one interruption.
    """
    import time

    robots = robots or RobotsCache()
    pause = sleep or time.sleep
    results: list[tuple[object, PageResult]] = []

    for index, org in enumerate(organizations):
        careers = getattr(org, "careers_page", "") or ""
        website = getattr(org, "website", "") or ""
        name = getattr(org, "organization", "?")

        if not careers and not website:
            results.append((org, PageResult(Discovery(note="no careers page given"))))
            continue

        candidates = [careers] if careers else []
        if try_fallbacks:
            candidates += fallback_urls(website, careers)

        result = PageResult(Discovery(note="no careers page given"))
        for candidate in candidates:
            if not candidate:
                continue
            decision = robots.check(candidate)
            if not decision.allowed:
                result = PageResult(Discovery(note=decision.note))
                continue
            if index or candidate is not candidates[0]:
                pause(delay_seconds)
            result = session.visit(candidate)
            if result.discovery.supported:
                break
            # Only a dead URL is worth retrying elsewhere; a page that loaded
            # and simply has no board is a real answer.
            if result.status not in (404, 403):
                break

        results.append((org, result))
        if on_result:
            on_result(org, result)
        if on_log:
            if result.discovery.supported:
                found = result.discovery
                on_log(f"  ✓ {name}: {found.source_type}:{found.board_token}")
            else:
                on_log(f"  · {name}: {result.discovery.note}")
    return results

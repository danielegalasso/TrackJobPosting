"""Browser-based discovery, for careers pages that are JavaScript apps.

The integration tests run a small site whose board token appears nowhere in
the initial HTML — the failure mode that accounts for roughly half of a real
import. They are skipped when no browser is available, so the suite still
runs on a machine without Playwright.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from acide.browser_discovery import (
    RobotsCache,
    deeper_board_links,
    fallback_urls,
    host_variant,
    resolve_with_browser,
)
from acide.discovery import discover_in_html

# ---------------------------------------------------------------------------
# A site that only reveals its board once scripts have run
# ---------------------------------------------------------------------------
SPA = (
    "<!doctype html><html><body><div id=board>Loading</div>"
    '<script src="/static/app.bundle.js"></script></body></html>'
)
BUNDLE = """
setTimeout(function () {
  var a = document.createElement('a');
  a.href = 'https://job-boards.green' + 'house.io/jsonlycorp';
  document.getElementById('board').appendChild(a);
}, 150);
"""
XHR = (
    "<!doctype html><html><body><div id=b>Loading</div>"
    '<script src="/static/loader.js"></script></body></html>'
)
STATIC = '<!doctype html><html><body><a href="https://jobs.lever.co/staticcorp">Jobs</a></body></html>'

# A careers landing page that says nothing about its ATS: the board is one
# click away, behind "See our open positions". This shape is the single
# largest failure mode on a real list — 200 of 631 "no ATS link found".
LANDING = (
    "<!doctype html><html><body><h1>Careers at Acme</h1>"
    "<p>We are hiring across Europe.</p>"
    '<a href="/about">About us</a>'
    '<a href="/landing/open-positions">See our open positions</a>'
    "</body></html>"
)
BOARD_PAGE = (
    '<!doctype html><html><body><a href="https://boards.greenhouse.io/hopcorp">'
    "Apply</a></body></html>"
)


def _make_handler(port: int):
    loader = (
        "fetch('/config.json').then(function (r) { return r.json(); }).then(function (cfg) {"
        f"  return fetch('http://127.0.0.1:{port}"
        "/proxy/api.ashbyhq.com/posting-api/job-board/' + cfg.board);"
        "}).then(function (r) { return r.json(); }).then(function (d) {"
        "  document.getElementById('b').textContent = d.jobs.length;"
        "});"
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, body, ctype="text/html", status=200):
            payload = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            path = self.path.split("?")[0]
            routes = {
                "/spa": (SPA, "text/html", 200),
                "/xhr": (XHR, "text/html", 200),
                "/static": (STATIC, "text/html", 200),
                "/landing": (LANDING, "text/html", 200),
                "/landing/open-positions": (BOARD_PAGE, "text/html", 200),
                "/blocked": ("<h1>Forbidden</h1>", "text/html", 403),
                "/static/app.bundle.js": (BUNDLE, "application/javascript", 200),
                "/static/loader.js": (loader, "application/javascript", 200),
                "/config.json": (json.dumps({"board": "xhrcorp"}), "application/json", 200),
                "/robots.txt": ("User-agent: *\nDisallow: /private\n", "text/plain", 200),
                "/private/careers": ("<h1>secret</h1>", "text/html", 200),
            }
            if path in routes:
                body, ctype, status = routes[path]
                return self._send(body, ctype, status)
            if path.startswith("/proxy/api.ashbyhq.com/posting-api/job-board/"):
                return self._send(json.dumps({"jobs": [{"id": 1}]}), "application/json")
            return self._send("<h1>Not found</h1>", status=404)

    return Handler


@pytest.fixture(scope="module")
def site():
    """A local site serving JavaScript-rendered careers pages."""
    server = HTTPServer(("127.0.0.1", 0), _make_handler(0))
    port = server.server_port
    server.RequestHandlerClass = _make_handler(port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()


def _browser_or_skip(**kwargs):
    """A browser session, or a skip when none is installed."""
    from acide.browser_discovery import BrowserSession, BrowserUnavailable

    pytest.importorskip("playwright", reason="browser mode needs Playwright")
    executable = os.environ.get("ACIDE_TEST_CHROME")
    if not executable:
        candidates = [
            "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
            "/usr/bin/google-chrome",
            "/usr/bin/chromium",
        ]
        executable = next((path for path in candidates if os.path.exists(path)), None)
    try:
        session = BrowserSession(executable_path=executable, **kwargs)
        return session.__enter__()
    except BrowserUnavailable as exc:
        pytest.skip(f"no browser available: {exc}")


# ---------------------------------------------------------------------------
# The core claim: a browser sees what plain HTTP cannot
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(("path", "source_type", "token"), [
    ("/spa", "greenhouse", "jsonlycorp"),
    ("/xhr", "ashby", "xhrcorp"),
    ("/static", "lever", "staticcorp"),
])
def test_browser_resolves_pages_that_plain_http_cannot(site, path, source_type, token):
    import httpx

    raw = httpx.get(f"{site}{path}").text
    plain = discover_in_html(raw)
    if path != "/static":
        # Establish the premise: the token really is absent from the HTML.
        assert not plain.supported, "fixture no longer reproduces the JS-only case"

    session = _browser_or_skip(settle_ms=1200)
    try:
        found = session.visit(f"{site}{path}").discovery
    finally:
        session.close()

    assert found.supported
    assert (found.source_type, found.board_token) == (source_type, token)


def test_the_request_log_finds_a_token_that_never_reaches_the_dom(site):
    """The /xhr page only ever calls the ATS; nothing is rendered with it."""
    session = _browser_or_skip(settle_ms=1200)
    try:
        result = session.visit(f"{site}/xhr")
    finally:
        session.close()
    assert result.discovery.board_token == "xhrcorp"


def test_a_refusal_is_reported_not_worked_around(site):
    session = _browser_or_skip(settle_ms=300)
    try:
        result = session.visit(f"{site}/blocked")
    finally:
        session.close()
    assert result.status == 403
    assert result.discovery.supported is False
    assert "403" in result.discovery.note


def test_a_page_that_does_not_load_is_reported(site):
    session = _browser_or_skip(settle_ms=300)
    try:
        result = session.visit("http://127.0.0.1:1/nothing-here")
    finally:
        session.close()
    assert result.discovery.supported is False


# ---------------------------------------------------------------------------
# Stale-URL fallbacks
# ---------------------------------------------------------------------------
def test_fallbacks_offer_other_common_careers_paths():
    urls = fallback_urls("https://www.acme.com", "https://www.acme.com/old-jobs")
    assert "https://www.acme.com/careers" in urls
    assert "https://www.acme.com/jobs" in urls


def test_fallbacks_do_not_repeat_the_url_that_already_failed():
    urls = fallback_urls("https://www.acme.com", "https://www.acme.com/careers")
    # The dead path itself is skipped, trailing slash and all...
    assert "https://www.acme.com/careers" not in urls
    assert "https://www.acme.com/careers/" not in urls
    # ...but a genuinely different path that merely ends the same way stays.
    assert "https://www.acme.com/en/careers" in urls


def test_fallbacks_use_the_website_host_not_the_dead_careers_host():
    """The www variant of the website counts as the same site; the dead
    careers host must not be chased at all, in either form."""
    from urllib.parse import urlparse

    urls = fallback_urls("https://acme.com", "https://gone.example/careers")
    hosts = {urlparse(url).netloc for url in urls}
    assert hosts <= {"acme.com", "www.acme.com"}
    assert not any("gone.example" in url for url in urls)


def test_fallbacks_are_empty_without_anything_to_go_on():
    assert fallback_urls("", "") == []


# ---------------------------------------------------------------------------
# robots.txt
# ---------------------------------------------------------------------------
class _RobotsSite:
    """A host serving one robots.txt response, recording how it was asked."""

    def __init__(self, status: int, body: str = ""):
        self.status, self.body, self.agents = status, body, []
        site = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                site.agents.append(self.headers.get("User-Agent", ""))
                if self.path != "/robots.txt":
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"<h1>Careers</h1>")
                    return
                self.send_response(site.status)
                self.end_headers()
                self.wfile.write(site.body.encode())

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.server.shutdown()


def test_robots_is_respected(site):
    """A person is not bound by robots.txt; six hundred scripted visits are."""
    robots = RobotsCache()
    assert robots.allowed(f"{site}/spa") is True
    assert robots.allowed(f"{site}/private/careers") is False


def test_a_host_without_robots_is_allowed():
    robots = RobotsCache()
    # Nothing is listening, so robots.txt cannot be read: nothing forbids us.
    assert robots.allowed("http://127.0.0.1:1/careers") is True


def test_robots_is_fetched_once_per_host():
    """Six hundred pages must not mean six hundred robots.txt requests."""
    with _RobotsSite(200, "User-agent: *\nAllow: /\n") as site:
        robots = RobotsCache()
        robots.allowed(f"{site.url}/careers")
        robots.allowed(f"{site.url}/jobs")
        robots.allowed(f"{site.url}/about/careers")
    assert len(site.agents) == 1, "robots.txt was re-fetched for the same host"


# ---------------------------------------------------------------------------
# An unreadable robots.txt is not a refusal
# ---------------------------------------------------------------------------
def test_a_waf_blocking_robots_txt_is_not_a_refusal():
    """The bug this replaces cost 125 of a real 631-entry list.

    Security vendors run WAFs, WAFs reject `Python-urllib`, and
    RobotFileParser treats a 403 as disallow-all — so the sites most likely
    to block the fetch were exactly the ones reported as forbidding us,
    having forbidden nothing. RFC 9309 §2.3.1.4 treats 4xx as "no
    robots.txt applies".
    """
    with _RobotsSite(403) as site:
        assert RobotsCache().allowed(f"{site.url}/careers") is True


def test_robots_txt_is_fetched_with_an_agent_a_waf_will_answer():
    with _RobotsSite(200, "User-agent: *\nAllow: /\n") as site:
        RobotsCache().allowed(f"{site.url}/careers")
    assert "Python-urllib" not in site.agents[0]
    assert "ACIDE-Watch" in site.agents[0], "and still identifies itself"


def test_a_robots_txt_that_can_be_read_is_still_obeyed():
    """The boundary has not moved: a real refusal is a refusal."""
    with _RobotsSite(200, "User-agent: *\nDisallow: /careers\n") as site:
        decision = RobotsCache().check(f"{site.url}/careers")
    assert decision.allowed is False
    assert decision.note == "disallowed by robots.txt"


def test_a_readable_robots_txt_that_permits_us_allows_the_visit():
    with _RobotsSite(200, "User-agent: *\nDisallow: /admin\n") as site:
        assert RobotsCache().allowed(f"{site.url}/careers") is True


def test_a_server_error_on_robots_txt_is_treated_as_disallow():
    """RFC 9309 does say to assume disallow when robots.txt is unreachable."""
    with _RobotsSite(503) as site:
        decision = RobotsCache().check(f"{site.url}/careers")
    assert decision.allowed is False
    assert "unreadable" in decision.note


def test_a_missing_robots_txt_allows_everything():
    with _RobotsSite(404) as site:
        assert RobotsCache().allowed(f"{site.url}/careers") is True


# ---------------------------------------------------------------------------
# The board one click past the careers page
# ---------------------------------------------------------------------------
def test_deeper_links_prefer_the_promise_of_actual_roles():
    html = (
        '<a href="/about">About us</a>'
        '<a href="/careers/open-positions">See our open positions</a>'
        '<a href="/news">Newsroom</a>'
    )
    links = deeper_board_links(html, "https://acme.com/careers")
    assert links[0] == "https://acme.com/careers/open-positions"


def test_deeper_links_stay_on_the_same_host():
    """A LinkedIn mirror is not this company's board."""
    html = (
        '<a href="https://www.linkedin.com/company/acme/jobs/">Open positions</a>'
        '<a href="/careers/vacancies">Open positions</a>'
    )
    links = deeper_board_links(html, "https://acme.com/careers")
    assert links == ["https://acme.com/careers/vacancies"]


def test_deeper_links_never_point_back_at_the_current_page():
    html = '<a href="/careers">Careers</a><a href="/careers/">Jobs</a>'
    assert deeper_board_links(html, "https://acme.com/careers") == []


def test_deeper_links_are_empty_when_nothing_promises_roles():
    html = '<a href="/about">About</a><a href="/press">Press</a>'
    assert deeper_board_links(html, "https://acme.com/careers") == []


def test_a_board_one_click_away_is_found(site):
    """The landing page names no ATS; the page behind its button does."""
    session = _browser_or_skip(settle_ms=600)
    organizations = [SimpleNamespace(organization="Hop", careers_page=f"{site}/landing")]
    try:
        [(_, result)] = resolve_with_browser(
            session, organizations, try_fallbacks=False, delay_seconds=0
        )
    finally:
        session.close()
    assert result.discovery.supported, result.discovery.note
    assert result.discovery.source_type == "greenhouse"
    assert result.discovery.board_token == "hopcorp"


def test_the_hop_is_not_taken_when_the_page_already_answered(site):
    session = _browser_or_skip(settle_ms=600)
    visited: list[str] = []
    organizations = [SimpleNamespace(organization="Static", careers_page=f"{site}/static")]
    try:
        real_visit = session.visit

        def counting_visit(url):
            visited.append(url)
            return real_visit(url)

        session.visit = counting_visit  # type: ignore[method-assign]
        resolve_with_browser(session, organizations, try_fallbacks=False, delay_seconds=0)
    finally:
        session.close()
    assert len(visited) == 1, "a page that resolved must not be followed further"


# ---------------------------------------------------------------------------
# One page, many organizations
# ---------------------------------------------------------------------------
def test_a_shared_careers_page_is_visited_once_for_the_whole_run(site):
    """Eight Thales divisions share one careers site; so do eight EU bodies.

    Visiting it once per division is slower and ruder for no new information.
    """
    session = _browser_or_skip(settle_ms=400)
    shared = f"{site}/static"
    organizations = [
        SimpleNamespace(organization=f"Division {n}", careers_page=shared) for n in range(5)
    ]
    visited: list[str] = []
    try:
        real_visit = session.visit

        def counting_visit(url):
            visited.append(url)
            return real_visit(url)

        session.visit = counting_visit  # type: ignore[method-assign]
        results = resolve_with_browser(
            session, organizations, try_fallbacks=False, delay_seconds=0
        )
    finally:
        session.close()

    assert len(visited) == 1, f"the shared page was fetched {len(visited)} times"
    # Every division still gets its own answer.
    assert len(results) == 5
    assert all(result.discovery.board_token == "staticcorp" for _, result in results)


# ---------------------------------------------------------------------------
# The other host form
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.argotec.it/careers/", "https://argotec.it/careers/"),
        ("https://dorbit.space/careers", "https://www.dorbit.space/careers"),
        ("https://acme.com", "https://www.acme.com"),
        ("", ""),
    ],
)
def test_host_variant_toggles_www(url, expected):
    """A certificate often covers one form and not the other."""
    assert host_variant(url) == expected


def test_fallbacks_try_the_other_host_form_first():
    """It is the cheapest possible fix for a cert or DNS failure."""
    candidates = fallback_urls("https://www.acme.com", "https://www.acme.com/careers")
    assert candidates[0] == "https://acme.com/careers"
    assert "https://acme.com" in candidates[:2]


def test_fallbacks_do_not_repeat_a_url():
    candidates = fallback_urls("https://acme.com", "https://acme.com/careers")
    assert len(candidates) == len(set(url.rstrip("/") for url in candidates))

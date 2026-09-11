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

import pytest

from acide.browser_discovery import RobotsCache, fallback_urls
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
    urls = fallback_urls("https://acme.com", "https://gone.example/careers")
    assert all(url.startswith("https://acme.com") for url in urls)


def test_fallbacks_are_empty_without_anything_to_go_on():
    assert fallback_urls("", "") == []


# ---------------------------------------------------------------------------
# robots.txt
# ---------------------------------------------------------------------------
def test_robots_is_respected(site):
    """A person is not bound by robots.txt; six hundred scripted visits are."""
    robots = RobotsCache()
    assert robots.allowed(f"{site}/spa") is True
    assert robots.allowed(f"{site}/private/careers") is False


def test_a_host_without_robots_is_allowed():
    robots = RobotsCache()
    # Nothing is listening, so robots.txt cannot be read: nothing forbids us.
    assert robots.allowed("http://127.0.0.1:1/careers") is True


def test_robots_is_fetched_once_per_host(site, monkeypatch):
    robots = RobotsCache()
    robots.allowed(f"{site}/spa")
    cached = dict(robots._parsers)
    robots.allowed(f"{site}/other")
    assert dict(robots._parsers) == cached

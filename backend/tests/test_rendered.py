"""Indexing a careers page with an interface of its own, in a real browser.

The fixture is deliberately hostile: the list is built by JavaScript, half of
it hides behind a "Load more" button, the landing page carries no structured
data, and each role is linked three times. That is the shape of the ~250
organizations no connector reaches.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import httpx
import pytest

from acide.models import TargetSource
from acide.spider.rendered import RenderedConnector, is_load_more

ROLES = [
    ("747f-it-infrastructure-administrator", "IT Infrastructure Administrator", "Bastogne, LX"),
    ("6f5d-data-intelligence-engineer", "Data Intelligence Engineer", "Noordwijk-Binnen, NL"),
    ("a85e-gnc-aocs-sensors-engineer", "GNC-AOCS Sensors and Actuators Engineer", "Noordwijk, NL"),
    ("fe29-safety-certification-engineer", "Safety and Certification Engineer", "Toulouse, FR"),
    ("ad3f-ground-segment-field-engineer", "Ground Segment Field Engineer", "Transinne, BE"),
]

LANDING = """<!doctype html><html><head><title>Careers — Spazio</title></head><body>
<nav><a href="/">Home</a><a href="/about">About us</a><a href="/contact">Contact us</a></nav>
<h1>We make space possible</h1>
<div id="list">Loading our positions…</div>
<button id="more">Load more roles</button>
<footer><a href="/privacy">Privacy policy</a><a href="/terms">Terms of use</a></footer>
<script src="/app.js"></script></body></html>"""

APP_JS = """
var ROLES = __ROLES__;
var shown = 0;
function draw() {
  var host = document.getElementById('list');
  var next = ROLES.slice(0, shown);
  host.innerHTML = next.map(function (r) {
    return '<li>' +
      '<a href="/p/' + r[0] + '">' + r[1] + '</a>' +
      '<a href="/p/' + r[0] + '">' + r[2] + '</a>' +
      '<a href="/p/' + r[0] + '">Full time</a>' +
      '</li>';
  }).join('');
  if (shown >= ROLES.length) { document.getElementById('more').style.display = 'none'; }
}
setTimeout(function () { shown = 2; draw(); }, 120);
document.getElementById('more').addEventListener('click', function () {
  shown = Math.min(shown + 2, ROLES.length);
  setTimeout(draw, 80);
});
""".replace("__ROLES__", json.dumps(ROLES))


def _posting_page(slug: str, title: str, location: str, *, structured: bool) -> str:
    if structured:
        payload = {
            "@context": "https://schema.org",
            "@type": "JobPosting",
            "title": title,
            "description": f"<p>You will work on {title.lower()}.</p>",
            "datePosted": "2026-09-01",
            "jobLocation": {"@type": "Place", "address": {
                "@type": "PostalAddress", "addressLocality": location.split(",")[0].strip(),
                "addressCountry": location.split(",")[-1].strip()}},
        }
        extra = f'<script type="application/ld+json">{json.dumps(payload)}</script>'
    else:
        extra = ""
    return (
        f"<!doctype html><html><head><title>{title} | Spazio</title>{extra}</head><body>"
        f"<nav><a href='/'>Home</a></nav><h1>{title}</h1>"
        f"<p>You will work on {title.lower()} in {location}.</p>"
        f"<footer>© Spazio</footer></body></html>"
    )


@pytest.fixture(scope="module")
def site():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/careers"):
                body, ctype = LANDING, "text/html"
            elif path == "/app.js":
                body, ctype = APP_JS, "application/javascript"
            elif path.startswith("/p/"):
                slug = path[3:]
                match = next((r for r in ROLES if r[0] == slug), None)
                if match is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                # Only the first role publishes structured data, so both
                # readings are exercised in one run.
                body = _posting_page(*match, structured=(slug == ROLES[0][0]))
                ctype = "text/html"
            else:
                self.send_response(404)
                self.end_headers()
                return
            payload = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def _session_or_skip(**kwargs):
    from acide.browser_discovery import BrowserSession, BrowserUnavailable

    pytest.importorskip("playwright", reason="rendered indexing needs Playwright")
    executable = os.environ.get("ACIDE_TEST_CHROME")
    if not executable:
        candidates = [
            "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
            "/usr/bin/google-chrome",
            "/usr/bin/chromium",
        ]
        executable = next((path for path in candidates if os.path.exists(path)), None)
    try:
        return BrowserSession(executable_path=executable, **kwargs).__enter__()
    except BrowserUnavailable as exc:
        pytest.skip(f"no browser available: {exc}")


def _run(site, session, **kwargs):
    with httpx.Client(timeout=10.0) as client:
        connector = RenderedConnector(client, request_delay=0, **kwargs)
        connector.session = session
        return list(connector.fetch(TargetSource(
            company="Spazio", source_type="browser", board_token=f"{site}/careers"
        )))


# ---------------------------------------------------------------------------
# The whole path, in a real browser
# ---------------------------------------------------------------------------
def test_a_javascript_only_list_is_indexed_end_to_end(site):
    """Nothing here is machine readable and it still produces postings."""
    raw = httpx.get(f"{site}/careers").text
    assert "IT Infrastructure Administrator" not in raw, "fixture no longer JS-only"

    session = _session_or_skip(settle_ms=700)
    try:
        postings = _run(site, session)
    finally:
        session.close()

    titles = sorted(posting.title for posting in postings)
    assert titles == sorted(role[1] for role in ROLES), titles
    # Each role was linked three times and must appear once.
    assert len(postings) == len(ROLES)
    # Navigation and footer are not roles.
    assert not any(word in " ".join(titles) for word in ("Privacy", "Contact", "Home"))


def test_the_load_more_button_is_clicked_until_the_list_is_whole(site):
    """Two of five roles render initially; the rest are behind the button."""
    session = _session_or_skip(settle_ms=700)
    try:
        postings = _run(site, session)
    finally:
        session.close()
    assert len(postings) == 5, "the list was not fully expanded"


def test_structured_data_is_used_where_a_posting_page_has_it(site):
    session = _session_or_skip(settle_ms=700)
    try:
        postings = _run(site, session)
    finally:
        session.close()
    by_title = {posting.title: posting for posting in postings}

    # The first role publishes JobPosting; its location comes out structured.
    structured = by_title["IT Infrastructure Administrator"]
    assert structured.location == "Bastogne, LX"
    assert structured.date_posted == "2026-09-01"

    # The others have none, so the heading and body are read instead — a
    # weaker reading, and the location is honestly left unknown.
    plain = by_title["Data Intelligence Engineer"]
    assert plain.location == "Not specified"
    assert "data intelligence engineer" in plain.description.lower()
    assert plain.apply_url.endswith("/p/6f5d-data-intelligence-engineer")


def test_search_terms_narrow_the_list_before_any_posting_is_opened(site):
    """The filter must run on the list, or every role costs a page render."""
    session = _session_or_skip(settle_ms=700)
    rendered: list[str] = []
    try:
        real = session.render

        def counting(url, **kwargs):
            rendered.append(url)
            return real(url, **kwargs)

        session.render = counting  # type: ignore[method-assign]
        postings = _run(site, session, search_terms=("safety",))
    finally:
        session.close()

    assert [posting.title for posting in postings] == ["Safety and Certification Engineer"]
    # One render for the list, one for the single matching posting.
    assert len(rendered) == 2, rendered


# ---------------------------------------------------------------------------
# Without a browser, and without a list
# ---------------------------------------------------------------------------
def test_without_a_session_it_says_what_to_install():
    from acide.spider.base import ConnectorError

    with httpx.Client() as client:
        connector = RenderedConnector(client, request_delay=0)
        with pytest.raises(ConnectorError, match="browser extra"):
            list(connector.fetch(TargetSource(
                company="A", source_type="browser", board_token="https://acme.example/careers"
            )))


def test_a_page_with_no_identifiable_list_says_so():
    from acide.spider.base import ConnectorError

    session = SimpleNamespace(
        render=lambda url, **kwargs: "<html><body><h1>Email us</h1></body></html>"
    )
    with httpx.Client() as client:
        connector = RenderedConnector(client, request_delay=0)
        connector.session = session
        with pytest.raises(ConnectorError, match="no job list could be identified"):
            list(connector.fetch(TargetSource(
                company="A", source_type="browser", board_token="https://acme.example/careers"
            )))


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Load more", True),
        ("Show more roles", True),
        ("Mehr laden", True),
        ("Carica altri", True),
        ("Voir plus", True),
        ("Apply now", False),
        ("Submit", False),
        ("", False),
    ],
)
def test_load_more_labels(label, expected):
    assert is_load_more(label) is expected

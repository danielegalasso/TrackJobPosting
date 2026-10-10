"""What every career-site extractor here shares, whatever platform it reads.

HTTP with retries, advert HTML to plain text, and the end of a run: open
the own page of any posting the source left without an advert, write
the postings to a timestamped JSON and CSV under <out_dir>/runs/, overwrite
<out_dir>/latest.{json,csv} when the run is good, and print what appeared or
disappeared since the previous run. A bad run is never mistaken for a good
one: exit status 1 when fewer postings came back than the site reports, 2
when none came back at all, and in both cases latest is left alone.

Standard library only.
"""

from __future__ import annotations

import contextlib
import csv
import http.client
import http.cookiejar
import html
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

#: One cookie jar for the run: some sites (SuccessFactors) tie the token a
#: page hands out to the session cookie that came with it.
#: One TLS context for the run, so a missing intermediate certificate can be
#: added to it (see trust_intermediate) without turning verification off.
_TLS = ssl.create_default_context()
_OPENER = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
    urllib.request.HTTPSHandler(context=_TLS),
)

#: Concurrent requests for the per-posting adverts.
DETAIL_WORKERS = 6

#: An advert shorter than this is taken for a stub (a list's teaser, a meta
#: description, an empty field) and the posting's own page is read for one.
SHORT_ADVERT = 300

_HEADERS = {
    # Keeps relative dates such as "Posted 3 Days Ago" in one language.
    "Accept-Language": "en-US",
    # A browser's: several career sites (Link11) answer a non-browser agent
    # with an empty shell, and the pages read here are the public ones a
    # browser is shown.
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0 Safari/537.36"
    ),
}


def _fetch(
    url: str,
    body: dict[str, Any] | None,
    accept: str,
    extra_headers: dict[str, str] | None = None,
    attempts: int = 4,
    *,
    form: bool = False,
) -> bytes:
    """GET (or POST when a body is given, as JSON or as a form), retrying transient failures."""
    # Links scraped from pages can carry raw non-ASCII ("Jobs_…_sarà"); a URL
    # on the wire must be percent-encoded, which leaves existing escapes be.
    url = urllib.parse.quote(url, safe=":/?#[]@!$&'()*+,;=%~")
    headers = {**_HEADERS, "Accept": accept, **(extra_headers or {})}
    data = None
    if body is not None and form:
        data = urllib.parse.urlencode(body).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(url, data=data, headers=headers)
        try:
            with _OPENER.open(request, timeout=60) as response:
                body = response.read()
                # Some servers compress without being asked (taleez.com).
                if response.headers.get("Content-Encoding") == "gzip" or body[:2] == b"\x1f\x8b":
                    import gzip

                    body = gzip.decompress(body)
                return body
        except urllib.error.HTTPError as exc:
            # 4xx other than rate limiting will not get better by retrying.
            if exc.code != 429 and exc.code < 500 or attempt == attempts:
                raise RuntimeError(f"{url}: HTTP {exc.code}") from exc
            if exc.code == 429:
                # Rate limited: a few seconds is not enough (EU Careers,
                # Eightfold refused dozens of adverts that way). Wait as long
                # as the server asks, or a little longer each time.
                time.sleep(_retry_after(exc) or 15 * attempt)
                continue
        # A server that drops the connection mid-run (RemoteDisconnected,
        # connection reset) usually answers the next attempt.
        except (urllib.error.URLError, TimeoutError, http.client.HTTPException, ConnectionError) as exc:
            if attempt == attempts:
                raise RuntimeError(f"{url}: {exc}") from exc
        time.sleep(2**attempt)
    raise AssertionError("unreachable")


def _retry_after(exc: urllib.error.HTTPError) -> int:
    """The seconds a 429's Retry-After asks for, at most two minutes; 0 if unstated."""
    value = (exc.headers.get("Retry-After") or "").strip() if exc.headers else ""
    return min(int(value), 120) if value.isdigit() else 0


def request_json(
    url: str, body: dict[str, Any] | None = None, headers: dict[str, str] | None = None
) -> Any:
    """GET (or POST when a body is given) a JSON document."""
    raw = _fetch(url, body, "application/json", headers)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{url}: response was not JSON") from exc


def request_form(url: str, fields: dict[str, str]) -> str:
    """POST an HTML form, as a page's own form submission would."""
    return _fetch(url, fields, "*/*", form=True).decode("utf-8", "replace")


def request_bytes(url: str) -> bytes:
    """GET a file as it is, a PDF say."""
    return _fetch(url, None, "*/*")


def trust_intermediate(der_url: str) -> None:
    """Accept certificates issued by the intermediate CA published at `der_url`.

    Some servers send their own certificate without the intermediate that
    links it to a root (vacancies.eda.europa.eu: "unable to get local issuer
    certificate"). Browsers fetch that intermediate from the address in the
    certificate's Authority Information Access field; this does the same.
    The intermediate is only trusted because it is signed by a root already
    in the system store, so the chain is still verified end to end.
    """
    with urllib.request.urlopen(der_url, timeout=60) as response:
        der = response.read()
    _TLS.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(der))


def pdf_text(url: str) -> str:
    """The text of a PDF, via pdftotext from poppler-utils; "" if unavailable."""
    if not shutil.which("pdftotext"):
        return ""
    try:
        pdf = request_bytes(url)
        result = subprocess.run(
            ["pdftotext", "-layout", "-", "-"], input=pdf, capture_output=True, timeout=120, check=True
        )
    except (RuntimeError, subprocess.SubprocessError, OSError) as exc:
        print(f"  ! could not read {url}: {exc}", file=sys.stderr)
        return ""
    text = result.stdout.decode("utf-8", "replace")
    lines = [re.sub(r"[ \t]{2,}", "  ", line).rstrip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


_BROWSERS = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")


def render_page(url: str, wait_ms: int = 15000) -> str:
    """The page as a browser shows it, after its scripts ran (headless Chrome).

    For career pages that build their list in the browser. A plain headless
    Chrome with a fresh profile: no pretending to be a person, so a page
    behind a bot check stays out of reach, as it should.
    """
    import tempfile

    browser = next((shutil.which(name) for name in _BROWSERS if shutil.which(name)), None)
    if browser is None:
        raise RuntimeError(f"{url}: needs a browser to render, and none of {', '.join(_BROWSERS)} is installed")
    with tempfile.TemporaryDirectory() as profile:
        try:
            result = subprocess.run(
                [
                    browser,
                    "--headless=new",
                    "--disable-gpu",
                    "--no-first-run",
                    "--no-default-browser-check",
                    f"--user-data-dir={profile}",
                    "--window-size=1400,2000",
                    f"--virtual-time-budget={wait_ms}",
                    "--dump-dom",
                    url,
                ],
                capture_output=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"{url}: the browser did not finish rendering") from exc
    page = result.stdout.decode("utf-8", "replace")
    if not page.strip():
        raise RuntimeError(f"{url}: the browser rendered nothing")
    return page


#: A persistent Chrome profile for the pages read past a bot check: the
#: clearance cookies survive between runs.
BROWSER_PROFILE = Path(__file__).resolve().parent / ".browser-profile"
#: Off screen on any system that honours a window position.
_OFF_SCREEN = ["--window-position=-32000,-32000", "--window-size=1280,800"]
#: What a bot check's interstitial says, where a page would be.
CHALLENGE_RE = re.compile(
    r"just a moment\.\.\.|quick security check|verify you are human|attention required|"
    r"request unsuccessful\. incapsula|px-captcha|are you a robot|access denied</title>",
    re.I,
)


@contextlib.contextmanager
def hidden_display():
    """Somewhere for a real (not headless) Chrome to draw that nobody sees.

    On Linux with Xvfb installed the window goes on a virtual display, which
    is invisible for certain. Elsewhere it is opened off screen; on Linux
    that needs X11 rather than Wayland, whose compositors place windows
    themselves (GNOME brings it back on screen), so Chrome is kept off
    Wayland. Yields the browser arguments.
    """
    if not sys.platform.startswith("linux"):
        yield _OFF_SCREEN
        return
    os.environ.pop("WAYLAND_DISPLAY", None)  # this process only: one per site
    xvfb = shutil.which("Xvfb")
    if not xvfb:
        yield _OFF_SCREEN
        return
    number = 99 + os.getpid() % 400  # a display of its own, as sites run side by side
    server = subprocess.Popen(
        [xvfb, f":{number}", "-screen", "0", "1920x1080x24", "-nolisten", "tcp"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    previous = os.environ.get("DISPLAY")
    os.environ["DISPLAY"] = f":{number}"
    try:
        time.sleep(1)  # let the server come up before Chrome connects
        yield ["--window-size=1920,1080"]
    finally:
        server.terminate()
        if previous is None:
            os.environ.pop("DISPLAY", None)
        else:
            os.environ["DISPLAY"] = previous


class StealthBrowser:
    """A real Chrome nobody sees, for pages behind a bot check.

    The operator asked (2026-10-10) for such sites to be read the way their
    test_for_cloudflare.py does: with nodriver, a Chrome without automation
    flags. It runs headless with a windowed Chrome's user agent: headless
    Chrome announces itself in its agent, and that is what the checks turn
    away. If a check still stops it, it starts again as a real window drawn
    where it cannot be seen (hidden_display). One browser serves every page
    of a run:

        with StealthBrowser() as browser:
            page = browser.get(url, ready=r"/careers/job/")
    """

    def __init__(self) -> None:
        self.headless = True
        self._display: Any = None

    def __enter__(self) -> StealthBrowser:
        try:
            import nodriver
        except ImportError as exc:
            raise RuntimeError("this site needs nodriver (pip install nodriver)") from exc
        self._nodriver = nodriver
        self._loop = nodriver.loop()
        self._start(headless=True)
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self._stop()

    def _start(self, *, headless: bool) -> None:
        nodriver = self._nodriver

        async def start() -> Any:
            if headless:
                probe = await nodriver.start(headless=True, user_data_dir=str(BROWSER_PROFILE))
                try:
                    agent = str(await probe.main_tab.evaluate("navigator.userAgent"))
                finally:
                    probe.stop()
                arguments = [f"--user-agent={agent.replace('HeadlessChrome', 'Chrome')}"]
            else:
                self._display = hidden_display()
                arguments = self._display.__enter__()
            return await nodriver.start(
                headless=headless, user_data_dir=str(BROWSER_PROFILE), browser_args=arguments
            )

        self.headless = headless
        self._browser = self._loop.run_until_complete(start())

    def _stop(self) -> None:
        if getattr(self, "_browser", None) is not None:
            self._browser.stop()
            self._browser = None
        if self._display is not None:
            self._display.__exit__(None, None, None)
            self._display = None

    def get(self, url: str, *, ready: str | None = None, wait: float = 45.0) -> str:
        """The page as the browser shows it, once past the check and, with
        `ready`, once that pattern appears (what the scripts build). A page
        whose `ready` never comes is returned as it stands: a list may be
        empty for real. One that stays on the check raises."""

        async def load() -> tuple[str, bool]:
            page = await self._browser.get(url)
            content = ""
            for _ in range(max(1, int(wait / 2.5))):
                await page.sleep(2.5)
                content = await page.get_content()
                if CHALLENGE_RE.search(content[:40000]):
                    continue
                if ready and not re.search(ready, content):
                    continue
                return content, True
            return content, False

        content, settled = self._loop.run_until_complete(load())
        blocked = not settled and bool(CHALLENGE_RE.search(content[:40000]))
        if blocked and self.headless:
            print(f"  headless turned away at {url}; retrying with a hidden window", file=sys.stderr)
            self._stop()
            self._start(headless=False)
            return self.get(url, ready=ready, wait=wait)
        if blocked:
            raise RuntimeError(f"{url}: the bot check did not pass")
        return content


def request_text(url: str) -> str:
    """GET an HTML page."""
    return _fetch(url, None, "text/html,application/xhtml+xml").decode("utf-8", "replace")


def html_to_text(fragment: str) -> str:
    """Advert HTML to readable plain text, keeping paragraph breaks."""
    # Some platforms ship the advert with its own <style> and <script> blocks
    # (Cornerstone); their contents are not text.
    fragment = re.sub(r"(?is)<(style|script)\b.*?</\1\s*>|<!--.*?-->", " ", fragment)
    # Break before a block as well as after it: an advert can run straight
    # from text into a heading ("…Kopf<h2>Deine Aufgaben</h2>").
    fragment = re.sub(r"(?i)<br\s*/?>|</?(?:p|div|ul|ol|h\d)\b[^>]*>|</li>", "\n", fragment)
    fragment = re.sub(r"(?i)<li[^>]*>", "- ", fragment)
    fragment = html.unescape(re.sub(r"<[^>]+>", "", fragment))
    lines = [re.sub(r"[ \t\xa0]+", " ", line).strip() for line in fragment.splitlines()]
    text = "\n".join(lines)
    # A list item whose text starts on the next source line leaves its bullet
    # alone on a line; put the text back beside it.
    text = re.sub(r"(?m)^-\n+(?=\S)", "- ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


class _ElementText(HTMLParser):
    """The inner HTML of every element carrying a given attribute value."""

    def __init__(self, attribute: str, value: str, token: bool) -> None:
        super().__init__(convert_charrefs=False)
        self.attribute, self.value, self.token = attribute, value, token
        self.depth = 0
        self.tag = ""
        self.parts: list[str] = []
        self.found: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.depth:
            if tag == self.tag:
                self.depth += 1
            self.parts.append(self.get_starttag_text() or "")
        elif any(name == self.attribute and self._matches(found or "") for name, found in attrs):
            self.tag, self.depth = tag, 1

    def _matches(self, found: str) -> bool:
        return self.value in found.split() if self.token else found == self.value

    def handle_endtag(self, tag: str) -> None:
        if not self.depth:
            return
        if tag == self.tag:
            self.depth -= 1
            if not self.depth:
                self.found.append("".join(self.parts))
                self.parts = []
                return
        self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self.depth:
            self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        self.handle_data(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.handle_data(f"&#{name};")


def inner_html(page: str, attribute: str, value: str, *, token: bool = False) -> list[str]:
    """Inner HTML of every element whose `attribute` is exactly `value`.

    With `token`, `value` need only be one of the attribute's space-separated
    words, as a CSS class selector matches. Counts nesting, so an element
    holding others of its own tag (an advert span full of spans) is cut out
    whole, as a regular expression cannot.
    """
    parser = _ElementText(attribute, value, token)
    parser.feed(page)
    return parser.found


_JSON_LD_RE = re.compile(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.S | re.I)


def json_ld_job(page: str) -> dict[str, Any]:
    """The schema.org JobPosting a page carries for search engines, or {}.

    Many career platforms embed one on every posting page (Personio, Breezy,
    Taleez, rexx, …), with title, dates, location and the full advert. It may
    sit alone, in a list, or in an @graph; PHP-written ones hold raw newlines.
    """
    for block in _JSON_LD_RE.findall(page):
        try:
            data = json.loads(block.strip(), strict=False)
        except json.JSONDecodeError:
            continue
        stack = [data]
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                kind = item.get("@type")
                if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
                    return item
                stack.extend(value for key, value in item.items() if key == "@graph")
    return {}


def json_ld_place(posting: dict[str, Any]) -> tuple[str, str]:
    """City (or region) and country of a JobPosting's first location."""
    places = posting.get("jobLocation") or []
    if isinstance(places, dict):
        places = [places]
    for place in places:
        address = (place or {}).get("address") or {}
        if isinstance(address, str):
            return address, ""
        def first(value: Any) -> Any:
            # Some sites give a list where one value is expected (["Bremen"]).
            return value[0] if isinstance(value, list) and value else value

        country = first(address.get("addressCountry")) or ""
        if isinstance(country, dict):
            country = country.get("name") or ""
        city = first(address.get("addressLocality")) or first(address.get("addressRegion")) or ""
        if city or country:
            return str(city), str(country)
    return "", ""


_MAIN_RE = re.compile(r"(?is)<(?:main|article)\b[^>]*>(.*)</(?:main|article)>")
_STRIP_RE = re.compile(r"(?is)<(header|nav|footer|aside|form|script|style|button|select)\b.*?</\1>")
_BLOCK_RE = re.compile(r"(?is)<(p|li|h[1-4]|dt|dd|td)\b[^>]*>(.*?)</\1>")


def page_main_text(page: str) -> str:
    """A posting page's text when nothing marks where the advert is.

    Paragraphs, list items and sub-headings, in order, from <main> or
    <article> if the page has one, without header, menus, footer and forms:
    on a utility-class page (Factorial) that is the advert with its facts,
    where taking the whole body would be mostly navigation.
    """
    match = _MAIN_RE.search(page)
    body = _STRIP_RE.sub(" ", match.group(1) if match else page)
    lines: list[str] = []
    for _, fragment in _BLOCK_RE.findall(body):
        text = html_to_text(fragment)
        if len(text) >= 3 and (not lines or lines[-1] != text):
            lines.append(text)
    text = "\n".join(lines)
    return text if len(text) >= 200 else html_to_text(body)


def advert_from_page(page: str) -> str:
    """The advert on a posting page, whatever platform wrote it.

    The page's JobPosting when it holds a real advert; otherwise the text of
    its <main> or <article>. A JobPosting's description is sometimes only
    the page's teaser (Defion), so the longer of the two wins when it is
    short. A page with neither gives nothing: its text would be the menus
    and cookie notices around an advert that is not there (Indra).
    """
    posting = html_to_text(html.unescape(str(json_ld_job(page).get("description") or "")))
    if len(posting) >= SHORT_ADVERT or not _MAIN_RE.search(page):
        return posting
    return max(posting, page_main_text(page), key=len)


def matches_query(query: str, *texts: str) -> bool:
    """Whether every word of `query` occurs in the texts (case-insensitive).

    For boards whose platform has no search of its own: the extractor applies
    the search the user's link carried, as plainly as possible.
    """
    words = [word for word in re.split(r"\W+", query.lower()) if word]
    haystack = " ".join(texts).lower()
    return all(word in haystack for word in words)


# ------------------------------------------------------------- end of a run


def _write(
    out_dir: Path, slug: str, stamp: str, records: list[dict[str, Any]], meta: dict[str, Any], *, latest: bool
) -> Path:
    runs = out_dir / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    document = {**meta, "postings": records}
    # Columns in the order the source's record builder lists them.
    fields = list(records[0]) if records else ["url"]
    targets = [runs / f"{slug}_{stamp}"] + ([out_dir / "latest"] if latest else [])
    for base in targets:
        base.with_suffix(".json").write_text(
            json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        with base.with_suffix(".csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(records)
    return runs / f"{slug}_{stamp}.csv"


def _previous(out_dir: Path, query: str) -> dict[str, dict[str, Any]] | None:
    latest = out_dir / "latest.json"
    if not latest.exists():
        return None
    document = json.loads(latest.read_text(encoding="utf-8"))
    if document.get("query") != query:
        return None
    return {posting["url"]: posting for posting in document.get("postings", [])}


def _report_changes(before: dict[str, dict[str, Any]] | None, records: list[dict[str, Any]]) -> None:
    if before is None:
        print("\nNo previous run for this query to compare against.")
        return
    now = {record["url"]: record for record in records}
    added = [now[key] for key in now.keys() - before.keys()]
    removed = [before[key] for key in before.keys() - now.keys()]
    print(f"\nSince the previous run: {len(added)} new, {len(removed)} gone.")
    for label, group in (("+", added), ("-", removed)):
        for record in sorted(group, key=lambda item: item["title"]):
            where = f"  ({record['location']})" if record.get("location") else ""
            print(f"  {label} {record.get('job_id', '')}  {record['title']}{where}")


def _page_advert(url: str) -> str:
    """The advert on a posting's own page, opened as a person would open it.

    The page as served first; then, when that came back with no advert, as a
    browser shows it after its scripts ran. A page that refused the request
    (403 behind a bot check, 404) is not rendered: the browser would only be
    shown the same refusal, as text that looks like an advert. Nor is one
    whose JobPosting is there but empty (Workday, Lever): the advert is
    empty at the source, and the rendered page would only add its chrome.
    """
    if urllib.parse.urlsplit(url).path.lower().endswith(".pdf"):
        return pdf_text(url)
    try:
        page = request_text(url)
    except RuntimeError as exc:
        print(f"  ! no advert for {url}: {exc}", file=sys.stderr)
        return ""
    advert = advert_from_page(page)
    if len(advert) >= SHORT_ADVERT or json_ld_job(page) or not any(shutil.which(n) for n in _BROWSERS):
        return advert
    try:
        return max(advert, advert_from_page(render_page(url)), key=len)
    except RuntimeError as exc:
        print(f"  ! no advert for {url}: {exc}", file=sys.stderr)
        return advert


def _about(title: str, text: str) -> bool:
    """Whether `text` names any word of the posting's title.

    An advert does; an error page ("please try again later", which europa.eu
    sends with status 200 when it is rate limiting), a login or a cookie
    wall does not.
    """
    words = [word for word in re.findall(r"\w{4,}", title.lower()) if not word.isdigit()]
    return not words or any(word in text.lower() for word in words)


def fill_adverts(records: list[dict[str, Any]], before: dict[str, dict[str, Any]] | None) -> int:
    """Give every posting with no advert, or only a stub, the one on its own page.

    Whatever the platform, a posting's URL is a page with its advert on it,
    so when the source's data left it out (a list with titles only, a field
    sent empty, a request refused mid-run) the page is opened, one posting at
    a time. The previous run's advert for the same URL is used first, when
    it is a real one, so a page is opened once rather than on every run.
    A record whose `vacancy_url` names the employer's own page for it (a
    job board that only relays the posting, as EU Careers does) is read
    there first. URLs with an anchor are postings written out on a shared
    page, whose page is the whole list, not their advert. A text is only
    taken when it names the posting's title. Returns how many were filled.
    """
    def short(text: str) -> bool:
        return len(text.strip()) < SHORT_ADVERT

    todo = [r for r in records if short(r.get("description") or "") and "#" not in r["url"]]
    if not todo:
        return 0

    def fill(record: dict[str, Any]) -> bool:
        current = (record.get("description") or "").strip()

        def fits(text: str) -> bool:
            # A text shorter than a real advert is more often the page's
            # menus than the advert, and never replaces what the source gave.
            # The posting's own stub inside it vouches for it as well as its
            # title does (NXP: "To man FE equipment" in "BGA FE Assembler").
            if short(text) or len(text) <= len(current):
                return False
            return _about(record["title"], text) or bool(current and current[:200] in text)

        elsewhere = str(record.get("vacancy_url") or "")
        pages = ([elsewhere] if elsewhere.startswith("http") and "#" not in elsewhere else []) + [record["url"]]
        found = str(((before or {}).get(record["url"]) or {}).get("description") or "").strip()
        for url in pages:
            if fits(found):
                break
            found = _page_advert(url).strip()
        if not fits(found):
            return False
        record["description"] = found
        return True

    print(f"\nOpening {len(todo)} postings whose advert the source left out…")
    # Fewer at a time than the first pass: these are the requests a server
    # may already have refused once.
    with ThreadPoolExecutor(max_workers=3) as pool:
        filled = sum(pool.map(fill, todo))
    return filled


@dataclass
class Collected:
    """What a platform module found for one board, before it is saved."""

    records: list[dict[str, Any]]
    #: The count the site states, or the size of an independent listing.
    total: int
    #: Postings whose advert could not be fetched.
    missing_detail: int = 0
    notes: list[str] = field(default_factory=list)
    #: Nothing found, and that is known to be true rather than a page that no
    #: longer parses: a structured API answered with an empty list, or the
    #: board was read and a search narrowed it to nothing.
    confirmed_empty: bool = False


def finish_run(
    *,
    name: str,
    out_dir: Path,
    query: str,
    source: str,
    started: datetime,
    total: int,
    records: list[dict[str, Any]],
    missing_detail: int,
    company: str | None = None,
    confirmed_empty: bool = False,
) -> int:
    """Save, compare with the previous run, and return the exit status.

    Each record needs `job_id`, `title`, `location` and `url`; the url is what
    tells postings apart between runs.
    """
    records.sort(key=lambda record: (record["title"].lower(), record["url"]))
    before = _previous(out_dir, query)
    filled = fill_adverts(records, before)
    missing_detail = min(missing_detail, sum(1 for r in records if not (r.get("description") or "").strip()))
    meta = {
        "source": source,
        **({"company": company} if company else {}),
        "query": query,
        "extracted_at": started.isoformat(timespec="seconds"),
        "site_total": total,
        "extracted": len(records),
    }
    # A run that came back short, or with nothing at all, is kept in runs/ for
    # inspection but does not replace latest: latest stays the last good run,
    # so the next comparison is against something real. Nothing at all may be
    # true (a small employer with no openings) but is more often a page that
    # changed shape so that both the postings and the count went unread.
    incomplete = len(records) < total
    # An empty result the source vouches for is a real result: saved as
    # latest, so postings that have closed leave the portal too.
    empty = not records and not confirmed_empty
    csv_path = _write(
        out_dir,
        name.lower(),
        started.strftime("%Y%m%d-%H%M%S"),
        records,
        meta,
        latest=not (incomplete or empty),
    )

    print(f"\nExtracted {len(records)} of {total} postings the site reports.")
    if filled:
        print(f"  {filled} adverts taken from the posting's own page.")
    if missing_detail:
        print(f"  {missing_detail} postings have no advert text (detail request failed).")
    print(f"  {csv_path}")
    if incomplete or empty:
        print(f"  {out_dir / 'latest.csv'} left as the last good run")
    else:
        print(f"  {out_dir / 'latest.csv'}")
    _report_changes(before, records)

    if incomplete:
        print(f"\nINCOMPLETE: {total - len(records)} postings were not extracted.", file=sys.stderr)
        return 1
    if not records and confirmed_empty:
        print("\nNo postings: the source was read and lists none at the moment.")
    if empty:
        print(
            "\nNOTHING FOUND: the site lists no postings, or no longer reads the way this"
            " extractor expects. Open the page to tell which.",
            file=sys.stderr,
        )
        return 2
    if before and len(before) >= 10 and len(before.keys() - {r["url"] for r in records}) > len(before) / 2:
        print(
            "\nCHECK: more than half of the previous run's postings are gone. That can be"
            " real, but is worth a look at the page.",
            file=sys.stderr,
        )
    return 0

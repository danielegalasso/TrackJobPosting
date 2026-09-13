"""Find the job list on a page nobody has written a connector for.

Around 250 organizations on a real list render their postings with JavaScript
behind an interface of their own: no ATS, no feed, and no schema.org markup on
the landing page. What they do have is a *list*, and a list has a shape.

Two pure functions here, both working on rendered HTML so they can be tested
without a browser:

`posting_links` finds the group of links that is the job list, by clustering
links on the shape of their URL rather than on any site's particular markup.
Every posting in a list shares a path prefix and differs only in its final
segment — `/careers/p/747f-it-administrator`, `/careers/p/6f5d-data-engineer`
— which is true of Breezy, of a hand-built Vue page, and of anything else that
puts one URL per role. A navigation bar shares a shape too, so the group's link
text has to look like job titles rather than like "About" and "Contact".

`read_posting` reads one posting page: its own JSON-LD if it has any, then Open
Graph, then the visible heading. A posting page carries JobPosting markup far
more often than a landing page does, which is where the earlier attempt at this
went wrong.
"""

from __future__ import annotations

import html as html_module
import re
import statistics
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

#: Whole path segments that mark a prefix as job-shaped. These are matched
#: exactly, never as substrings: `p` is Breezy's posting prefix, and testing
#: for it inside a segment would make "company" and "press" job-shaped.
_JOB_SEGMENTS = frozenset({
    "p", "o", "j", "job", "jobs", "career", "careers", "carriere", "carrieres",
    "vacancy", "vacancies", "vacature", "vacatures", "position", "positions",
    "opening", "openings", "opportunity", "opportunities", "stelle", "stellen",
    "offre", "offres", "emploi", "emplois", "lavoro", "empleo", "empleos",
    "recruitment", "apply", "join", "join-us", "werken-bij", "lavora-con-noi",
})
#: Longer stems, matched inside a segment, for paths like `job-openings`.
_JOB_STEMS = (
    "career", "job", "vacanc", "vacature", "position", "opening",
    "opportunit", "stelle", "emploi", "lavor", "empleo", "recruit",
)
#: Link text that is chrome, never a job title.
_CHROME = frozenset({
    "home", "about", "about us", "contact", "contact us", "news", "blog",
    "privacy", "privacy policy", "terms", "cookies", "cookie policy", "imprint",
    "impressum", "search", "menu", "login", "log in", "sign in", "register",
    "apply", "apply now", "read more", "more", "learn more", "details", "next",
    "previous", "back", "all", "view all", "see all", "share", "linkedin",
    "twitter", "facebook", "instagram", "youtube", "english", "deutsch",
    "français", "italiano", "español", "nederlands",
})

_LINK_RE = re.compile(
    r"""<a\b[^>]*?href\s*=\s*["']([^"']+)["'][^>]*>(.*?)</a>""",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_H1_RE = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_TITLE_RE = re.compile(r"<title\b[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_META_RE = re.compile(
    r"""<meta\b[^>]*?(?:property|name)\s*=\s*["']([^"']+)["'][^>]*?content\s*=\s*["']([^"']*)["']""",
    re.IGNORECASE,
)
_META_REVERSED = re.compile(
    r"""<meta\b[^>]*?content\s*=\s*["']([^"']*)["'][^>]*?(?:property|name)\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE,
)


def text_of(markup: str) -> str:
    """Visible text of a markup fragment, whitespace collapsed."""
    return _WS_RE.sub(" ", html_module.unescape(_TAG_RE.sub(" ", markup))).strip()


@dataclass(frozen=True)
class PostingLink:
    url: str
    text: str


def _shape(url: str) -> tuple[str, str, int] | None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None
    segments = [segment for segment in parsed.path.split("/") if segment]
    if not segments:
        return None
    # Everything but the final segment is the prefix every sibling shares.
    return (parsed.netloc.lower(), "/".join(segments[:-1]).lower(), len(segments))


def _is_job_prefix(prefix: str) -> bool:
    """Does this shared path prefix say the links below it are roles?"""
    for segment in (part for part in prefix.split("/") if part):
        if segment in _JOB_SEGMENTS:
            return True
        if any(stem in segment for stem in _JOB_STEMS):
            return True
    return False


def _looks_like_a_title(text: str) -> bool:
    lowered = text.strip().lower()
    if lowered in _CHROME or len(lowered) < 6:
        return False
    return len(lowered.split()) >= 2


def posting_links(html: str, page_url: str, limit: int = 200) -> list[PostingLink]:
    """The group of links on this page that is its job list, best first.

    Groups are judged on how many links share a URL shape, whether the shared
    prefix is job-shaped, and whether the link text reads like job titles. A
    single link is never a list.
    """
    if not html:
        return []

    groups: dict[tuple[str, str, int], dict[str, str]] = {}
    for href, inner in _LINK_RE.findall(html):
        href = href.strip()
        if not href or href.lower().startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = urljoin(page_url, href).split("#")[0]
        shape = _shape(absolute)
        if shape is None or absolute.rstrip("/") == page_url.rstrip("/"):
            continue
        text = text_of(inner)
        # One posting is often linked several times over — title, location,
        # contract type. Keep the longest text, which is the title.
        bucket = groups.setdefault(shape, {})
        if len(text) > len(bucket.get(absolute, "")):
            bucket[absolute] = text

    candidates: list[tuple[bool, float, list[PostingLink]]] = []
    for (_, prefix, _), members in groups.items():
        if len(members) < 2:
            continue
        entries = [PostingLink(url=url, text=text) for url, text in members.items()]
        titled = [entry for entry in entries if _looks_like_a_title(entry.text)]
        if len(titled) < 2:
            continue
        # Titles are sentences, not labels.
        median = statistics.median(len(entry.text) for entry in titled)
        score = len(titled) * (1.5 if median >= 15 else 1.0)
        candidates.append((_is_job_prefix(prefix), score, titled))

    if not candidates:
        return []
    # A job-shaped path is strong evidence; a big group is weak evidence. A
    # newsroom is routinely longer than the job list, so size must not be able
    # to outvote the path — it only breaks ties within the stronger class.
    job_shaped = [entry for entry in candidates if entry[0]]
    pool = job_shaped or candidates
    return max(pool, key=lambda entry: entry[1])[2][:limit]


# ---------------------------------------------------------------------------
# One posting page
# ---------------------------------------------------------------------------
@dataclass
class PageFacts:
    """What could be read off one posting page."""

    title: str = ""
    description: str = ""
    location: str = ""
    date_posted: str | None = None
    apply_url: str = ""
    amount: float | None = None
    currency: str | None = None
    rate: str | None = None
    #: Which reading produced this, for the log and the report.
    source: str = ""

    @property
    def usable(self) -> bool:
        """A title is the minimum; a posting with no title is not a posting."""
        return bool(self.title)


def meta_tags(html: str) -> dict[str, str]:
    """Every `<meta>` name/property and its content, either attribute order."""
    tags: dict[str, str] = {}
    for key, value in _META_RE.findall(html or ""):
        tags.setdefault(key.strip().lower(), html_module.unescape(value).strip())
    for value, key in _META_REVERSED.findall(html or ""):
        tags.setdefault(key.strip().lower(), html_module.unescape(value).strip())
    return tags


def _main_text(html: str, limit: int = 12000) -> str:
    """The page's readable body, scripts and styles removed."""
    body = re.sub(r"(?is)<(script|style|noscript|svg)\b.*?</\1>", " ", html or "")
    body = re.sub(r"(?is)<(nav|header|footer|aside)\b.*?</\1>", " ", body)
    body = re.sub(r"(?i)<br\s*/?>", "\n", body)
    body = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", "\n", body)
    text = html_module.unescape(_TAG_RE.sub(" ", body))
    lines = [_WS_RE.sub(" ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)[:limit]


def read_posting(html: str, url: str) -> PageFacts:
    """Read one posting page, in descending order of how much is really known.

    1. **schema.org JobPosting**, if the page carries it. Structured, and what
       Google for Jobs consumes, so a posting page very often does.
    2. **Open Graph**, which a page emits for its own link previews.
    3. **The visible heading**, which is a guess about layout and labelled as
       one so a reader of the report knows how much to trust it.
    """
    from .spider.jsonld import blocks, walk

    for document in blocks(html):
        for node in walk(document):
            facts = _from_jsonld(node, url)
            if facts.usable:
                return facts

    tags = meta_tags(html)
    body = _main_text(html)
    heading = text_of(_H1_RE.search(html).group(1)) if _H1_RE.search(html) else ""
    og_title = tags.get("og:title") or ""
    page_title = text_of(_TITLE_RE.search(html).group(1)) if _TITLE_RE.search(html) else ""

    title = heading or og_title or page_title
    description = tags.get("og:description") or tags.get("description") or ""
    if len(body) > len(description):
        description = body
    return PageFacts(
        title=_clean_title(title),
        description=description,
        location="",
        apply_url=tags.get("og:url") or url,
        source="the page's heading" if heading else "the page's metadata",
    )


def _clean_title(title: str) -> str:
    """Strip a trailing site name: "SOC Analyst | Acme" is one role."""
    for separator in (" | ", " – ", " — ", " :: "):
        if separator in title:
            head = title.split(separator)[0].strip()
            if len(head) >= 6:
                return head
    return title.strip()


def _from_jsonld(node: dict, url: str) -> PageFacts:
    from .spider.base import iso_date, strip_html
    from .spider.jsonld import _location, _pay

    amount, currency, rate = _pay(node)
    return PageFacts(
        title=str(node.get("title") or "").strip(),
        description=strip_html(str(node.get("description") or "")),
        location=_location(node),
        date_posted=iso_date(node.get("datePosted")),
        apply_url=str(node.get("url") or url),
        amount=amount,
        currency=currency,
        rate=rate,
        source="schema.org JobPosting",
    )

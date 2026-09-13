"""Index a careers page that has no ATS, no feed, and an interface of its own.

Around 250 organizations on a real 631-entry list land here: the page loads
fine, renders a list of roles with JavaScript, and offers nothing machine
readable. Every other connector in this package reads a published endpoint;
this one reads a page, which is a weaker contract and is treated as one.

How it works, per careers page:

1. Render it in a browser, let its requests go quiet, scroll, and click a
   "load more" control while one keeps appearing.
2. Find the job list generically, by clustering links on the shape of their
   URL — every posting in a list shares a path prefix and differs in its last
   segment — and checking the link text reads like job titles rather than like
   a navigation bar. See `acide.pagestructure`.
3. Open each posting and read it: its schema.org JobPosting if it has one,
   then Open Graph, then the visible heading. A posting page carries that
   markup far more often than a landing page does.

**This is the one connector that needs a browser while indexing**, so it is
opt-in per target and never reached unless an operator configures it. Nothing
else here renders anything on a schedule.

The boundary is the same as browser mode's: public pages, one at a time, at
human pace, robots.txt honoured, no fingerprint spoofing and no challenge
solving. A refusal is recorded as the answer.

Its token is the careers page URL.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlparse

from ..compensation import parse_compensation
from ..models import RawPosting, TargetSource
from ..pagestructure import posting_links, read_posting
from .base import Connector, ConnectorError, register

#: Controls that reveal the rest of a list. Clicked, not guessed around.
LOAD_MORE_WORDS = (
    "load more", "show more", "see more", "more jobs", "more positions",
    "view more", "mehr laden", "weitere", "voir plus", "carica altri",
    "mostra altri", "meer laden", "ver más", "next page",
)
#: How many times to click it. A list that needs more than this is unusual.
MAX_EXPANSIONS = 8
#: A posting page costs one render, so this is the real budget.
DEFAULT_POSTING_LIMIT = 60


@register
class RenderedConnector(Connector):
    source_type = "browser"
    token_hint = "The careers page URL, rendered in a browser"

    #: Set by the runner; without it this connector cannot work at all.
    session: Any = None

    def fetch(self, target: TargetSource) -> Iterable[RawPosting]:
        if self.session is None:
            raise ConnectorError(
                "the 'browser' source needs a rendering session. Install the "
                "browser extra (pip install -e 'backend/[browser]' && playwright "
                "install chromium) and run the inspector with browser indexing "
                "enabled."
            )

        page_url = target.board_token.strip()
        if "://" not in page_url:
            page_url = f"https://{page_url}"
        host = urlparse(page_url).netloc

        html = self._render(page_url, expand=True)
        links = posting_links(html, page_url, limit=DEFAULT_POSTING_LIMIT)
        if not links:
            raise ConnectorError(
                f"{page_url}: rendered, but no job list could be identified. The "
                "page may list roles without linking each one."
            )

        wanted = [link for link in links if self.wanted(link.text)]
        self.log(f"browser/{host}: {len(links)} roles listed, {len(wanted)} match")
        self.note_truncation(f"browser/{host}", len(wanted))

        seen: set[str] = set()
        for link in wanted[: self.max_jobs]:
            if link.url in seen:
                continue
            seen.add(link.url)
            try:
                posting_html = self._render(link.url, expand=False)
            except ConnectorError as exc:
                self.log(f"browser/{host}: {exc}", "warning")
                continue

            facts = read_posting(posting_html, link.url)
            # The list already gave a title; prefer it when the page gives none.
            title = facts.title or link.text
            if not title:
                continue
            amount, currency, rate = facts.amount, facts.currency, facts.rate
            if amount is None:
                amount, currency, rate = parse_compensation(facts.description[:4000])
            yield RawPosting(
                external_id=_identifier(link.url),
                company=target.company,
                title=title,
                location=facts.location or "Not specified",
                apply_url=facts.apply_url or link.url,
                description=facts.description,
                date_posted=facts.date_posted,
                source_type=self.source_type,
                amount=amount,
                currency=currency,
                rate=rate,
            )

    # -- rendering ----------------------------------------------------------
    def _render(self, url: str, *, expand: bool) -> str:
        """One page, rendered. Politeness and robots belong to the session."""
        self._throttle()
        try:
            return self.session.render(url, expand=expand)
        except Exception as exc:  # the session names its own failures
            raise ConnectorError(f"{url}: {exc}") from exc


def _identifier(url: str) -> str:
    return urlparse(url).path.rstrip("/").rsplit("/", 1)[-1] or url


_WORD_RE = re.compile("|".join(re.escape(word) for word in LOAD_MORE_WORDS), re.IGNORECASE)


def is_load_more(text: str) -> bool:
    """Does this control's label promise the rest of the list?"""
    return bool(text and _WORD_RE.search(text))

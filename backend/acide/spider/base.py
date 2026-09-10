"""Shared plumbing for ATS connectors.

Every connector reads a *published, documented job-board API* -- the same
JSON endpoint the employer's own careers page calls. That is a deliberate
design boundary: ACIDE-Watch does not drive a headless browser, does not
solve challenges, and does not work around bot defences. A source that
declines to serve us is reported as an error and skipped.
"""

from __future__ import annotations

import html
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from typing import Any

import httpx

from ..models import RawPosting, TargetSource

DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=10.0)


class ConnectorError(RuntimeError):
    """A source could not be indexed on this run."""


class Connector(ABC):
    """Base class for a single ATS integration."""

    source_type: str = ""
    #: Human-readable hint shown in the settings screen.
    token_hint: str = ""

    def __init__(
        self,
        client: httpx.Client,
        *,
        request_delay: float = 1.5,
        max_jobs: int = 120,
        on_log: Callable[[str, str], None] | None = None,
    ) -> None:
        self.client = client
        self.request_delay = request_delay
        self.max_jobs = max_jobs
        self._on_log = on_log
        self._last_request = 0.0

    # -- politeness ---------------------------------------------------------
    def _throttle(self) -> None:
        """Space requests out; we are a guest on someone else's server."""
        elapsed = time.monotonic() - self._last_request
        remaining = self.request_delay - elapsed
        if remaining > 0 and self._last_request:
            time.sleep(remaining)
        self._last_request = time.monotonic()

    def get_json(self, url: str, **kwargs: Any) -> Any:
        self._throttle()
        try:
            response = self.client.get(url, **kwargs)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"{url}: {exc}") from exc
        if response.status_code == 429:
            raise ConnectorError(f"{url}: rate limited by the source (429); backing off")
        if response.status_code == 404:
            raise ConnectorError(f"{url}: board not found (404) — check the board token")
        if response.status_code >= 400:
            raise ConnectorError(f"{url}: HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise ConnectorError(f"{url}: response was not JSON") from exc

    def log(self, message: str, level: str = "info") -> None:
        if self._on_log:
            self._on_log(message, level)

    # -- contract -----------------------------------------------------------
    @abstractmethod
    def fetch(self, target: TargetSource) -> Iterable[RawPosting]:
        """Yield postings for one configured company board."""


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")


def strip_html(raw: str) -> str:
    """Turn an ATS HTML description into plain text for the evaluator."""
    if not raw:
        return ""
    text = html.unescape(raw)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6])>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "• ", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


def iso_date(value: Any) -> str | None:
    """Normalise the assorted date shapes ATS feeds emit to `YYYY-MM-DD`."""
    if value in (None, ""):
        return None
    if isinstance(value, int | float):
        # Ashby and Lever publish epoch milliseconds.
        seconds = float(value) / 1000.0 if float(value) > 1e11 else float(value)
        try:
            return time.strftime("%Y-%m-%d", time.gmtime(seconds))
        except (ValueError, OSError):
            return None
    text = str(value).strip()
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        return match.group(0)
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%a, %d %b %Y %H:%M:%S", "%B %d, %Y", "%d %b %Y"):
        try:
            return time.strftime("%Y-%m-%d", time.strptime(text, fmt))
        except ValueError:
            continue
    return None


CONNECTORS: dict[str, type[Connector]] = {}


def register(cls: type[Connector]) -> type[Connector]:
    CONNECTORS[cls.source_type] = cls
    return cls


def get_connector(source_type: str) -> type[Connector]:
    try:
        return CONNECTORS[source_type]
    except KeyError as exc:
        raise ConnectorError(f"unknown source type: {source_type}") from exc

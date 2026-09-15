"""Group repeated failures by cause, so a summary says what went wrong.

A pass over several hundred sources fails the same way many times. One
malformed response schema had an overnight run rejected on every scoring
call, and the summary printed this:

    2798 error(s); the first few:
        Penetration Tester (All Gender): OpenRouter returned HTTP 400: …
        Ingénieur Support Produits Cyber (h/f): OpenRouter returned HTTP 400: …
        … thirteen more of the same …
        … and 2783 more

The count was right and the information was not there. Two things had gone
wrong that night — one schema rejected 2,783 scoring calls, and one careers
URL had rotted — and the second was invisible behind the first, because the
fifteen lines the summary had room for were fifteen copies of the same fault.

Grouping is on a *signature*: the message with the parts that differ between
occurrences removed, which in practice means the URL. Numbers are kept as
they are, because an HTTP 404 and an HTTP 403 are different diagnoses — one
says the page is gone, the other that a request was refused — and collapsing
them would lose exactly the distinction worth reading.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

_URL = re.compile(r"https?://\S+")
_WHITESPACE = re.compile(r"\s+")

#: How much of a message is used to tell one cause from another. Long enough
#: to separate two providers' error bodies, short enough that a tail carrying
#: a job title or an id does not split one cause into many.
SIGNATURE_LENGTH = 160


@dataclass
class Cause:
    """One kind of failure, and who ran into it."""

    #: The first message of this kind, in full — the exemplar shown to the
    #: operator, rather than the normalised signature it was grouped on.
    detail: str
    count: int = 0
    #: Whose failure it was: a company for a source, a title for a scoring
    #: call. In the order they were seen, without repeats.
    subjects: list[str] = field(default_factory=list)


def split_subject(message: str) -> tuple[str, str]:
    """Split "<subject>: <detail>" into its two halves.

    Errors are recorded with what failed in front of why: a company for a
    source that would not answer, a posting's title for a scoring call. Only
    the first separator counts — a detail carries colons of its own, as in
    `TU Munchen: https://www.tum.de/…: HTTP 404`.
    """
    subject, separator, detail = message.partition(": ")
    if not separator:
        return "", message.strip()
    return subject.strip(), detail.strip()


def signature(detail: str) -> str:
    """What makes two failures the same failure."""
    collapsed = _URL.sub("<url>", detail)
    return _WHITESPACE.sub(" ", collapsed).strip().lower()[:SIGNATURE_LENGTH]


def group(messages: Iterable[str]) -> list[Cause]:
    """Causes, most frequent first; first seen first among equals."""
    causes: dict[str, Cause] = {}
    for message in messages:
        subject, detail = split_subject(message)
        key = signature(detail)
        cause = causes.get(key)
        if cause is None:
            cause = causes[key] = Cause(detail=detail)
        cause.count += 1
        if subject and subject not in cause.subjects:
            cause.subjects.append(subject)
    # Stable, so equally frequent causes stay in the order they happened.
    return sorted(causes.values(), key=lambda cause: -cause.count)


def render(
    causes: Iterable[Cause],
    *,
    limit: int = 8,
    subjects: int = 4,
    width: int = 150,
) -> list[str]:
    """The lines a CLI prints for a grouped failure list."""
    causes = list(causes)
    lines: list[str] = []
    for cause in causes[:limit]:
        detail = cause.detail if len(cause.detail) <= width else cause.detail[: width - 1] + "…"
        lines.append(f"  {cause.count:5} ×  {detail}")
        named = cause.subjects[:subjects]
        if named:
            trailer = ", ".join(named)
            hidden = len(cause.subjects) - len(named)
            if hidden > 0:
                trailer += f", and {hidden} more"
            lines.append(f"           {trailer}")
    if len(causes) > limit:
        lines.append(f"  … and {len(causes) - limit} further cause(s)")
    return lines

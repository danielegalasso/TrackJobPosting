"""The operator's own exclusions: words a posting must not have.

The rules in ranking.py encode a judgement, and get it wrong now and then.
These encode a decision: a title saying "Senior" or "Stage" is one the
operator never wants to read, whatever else it says. So they are matched
literally, and a posting they catch is excluded like a visa or a sales role
— left out of the grid unless asked for, and never sent to the model.

Each entry is a word or a phrase, matched as whole words, ignoring case and
accents: "stage" catches "Stage ingénieur" but not "Backstage", "ingenieur"
catches "Ingénieur". A trailing `*` matches the start of a word, so
"intern*" catches "Intern", "Internship" and "Internal" alike.

They live in data/ranking-filters.json, next to the brief, and apply on the
next ranking pass; changing them re-applies them to every posting, which is
a matter of a second since nothing but the three fields is read.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any

from . import paths

#: The fields a filter reads, and how its exclusion reads in the grid.
FIELDS = {"title": "titolo", "company": "azienda", "location": "sede"}

#: Every exclusion reason a filter gives starts with this, so a reason left
#: by an old filter can be told from one the rules gave.
REASON_PREFIX = "Filtro "


@dataclass
class Filters:
    title: list[str] = field(default_factory=list)
    company: list[str] = field(default_factory=list)
    location: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, list[str]]:
        return asdict(self)

    def digest(self) -> str:
        """What the exclusions in the table were computed from."""
        payload = json.dumps(self.as_dict(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def fold(text: str) -> str:
    """Lowercased, without accents: what both sides of a match are made into."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _clean(entries: Any) -> list[str]:
    """Stripped, non-empty, each once (by its folded form), in the given order."""
    if isinstance(entries, str):
        entries = entries.splitlines()
    seen: set[str] = set()
    out: list[str] = []
    for entry in entries or []:
        text = " ".join(str(entry).split())
        key = fold(text)
        if key.rstrip("*").strip() and key not in seen:
            seen.add(key)
            out.append(text)
    return out


def load() -> Filters:
    try:
        raw = json.loads(paths.RANKING_FILTERS_PATH.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return Filters()
    if not isinstance(raw, dict):
        return Filters()
    return Filters(**{name: _clean(raw.get(name)) for name in FIELDS})


def save(data: dict[str, Any]) -> Filters:
    filters = Filters(**{name: _clean(data.get(name)) for name in FIELDS})
    paths.RANKING_FILTERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    paths.RANKING_FILTERS_PATH.write_text(
        json.dumps(filters.as_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return filters


def _pattern(entry: str) -> tuple[str, re.Pattern[str]]:
    """The folded literal a match must contain, and the whole-word pattern."""
    folded = fold(entry)
    prefix = folded.endswith("*")
    core = folded.rstrip("*").strip()
    # Spaces in a phrase stand for any run of whitespace or a hyphen:
    # "red team" catches "Red-Team" too.
    body = r"[\s\-]+".join(re.escape(word) for word in core.split())
    return core.split()[0], re.compile(rf"(?<!\w){body}" + ("" if prefix else r"(?!\w)"))


class Matcher:
    """The filters, compiled once for a pass over the table."""

    def __init__(self, filters: Filters) -> None:
        self.rules = [
            (name, entry, *_pattern(entry))
            for name in FIELDS
            for entry in getattr(filters, name)
        ]

    def __bool__(self) -> bool:
        return bool(self.rules)

    def reason(self, *, title: str = "", company: str = "", location: str = "") -> str:
        """Why the filters exclude the posting, or "" when they do not."""
        if not self.rules:
            return ""
        texts = {"title": fold(title), "company": fold(company), "location": fold(location)}
        for name, entry, anchor, pattern in self.rules:
            text = texts[name]
            if anchor in text and pattern.search(text):
                return f"{REASON_PREFIX}{FIELDS[name]}: {entry}"
        return ""

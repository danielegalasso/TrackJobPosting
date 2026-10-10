"""Bring the per-site extractors' results into the portal.

The extractors in ExtractFromTheCarrerPageTheJobs/ each write
`<Site>/latest.json` after a good run (a short or empty run leaves it
untouched), so that file is the current list of postings for its site. This
module mirrors those lists into the jobs table:

  * a posting in the file is inserted, or refreshed if already known, without
    touching the operator's star / removal or any evaluation it already has;
  * a posting of that site that is no longer in the file is marked closed,
    which takes it out of the grid but not out of "Starred" or "Removed".

`sync()` only re-reads a file whose modification time changed since the last
import, so it is cheap enough to call before every listing: a run of the
extractors shows up in the portal on the next page load, with nothing to
press.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import db, paths

logger = logging.getLogger(__name__)

SOURCE_TYPE = "extract"

#: Folder name -> how the company is shown. Folders not listed show as named.
DISPLAY_NAMES = {
    "CERTEU": "CERT-EU",
    "EDA": "European Defence Agency (EDA)",
    "ENISA": "ENISA",
    "EUCareers": "EU Careers (EPSO)",
    "Eurocontrol": "EUROCONTROL",
    "Genua": "genua",
    "ISS": "EU Institute for Security Studies (EUISS)",
    "NATO": "NATO",
    "NCCGroup": "NCC Group",
    "NVISO": "NVISO",
    "SatCen": "EU Satellite Centre (SatCen)",
    "Secunet": "secunet",
}

#: Record fields that can serve as the category, best first. Each extractor
#: names its own; these are the ones that read as a department or domain.
_CATEGORY_FIELDS = (
    "department",
    "category",
    "domain",
    "team",
    "division",
    "unit",
    "directorate",
    # Who hires, for sites that span several bodies (NATO, EU Careers).
    "organisation",
    "institution",
)
#: Record fields that can serve as the level shown on the card, best first.
_LEVEL_FIELDS = ("grade", "experience", "contract")
#: Record fields that carry the publication date, best first.
_POSTED_FIELDS = ("published", "start_date")

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_lock = threading.Lock()


@dataclass(frozen=True)
class SiteImport:
    site: str
    company: str
    postings: int
    new: int
    closed: int


def _first(record: dict[str, Any], fields: tuple[str, ...]) -> str:
    for field in fields:
        value = str(record.get(field) or "").strip()
        if value:
            return value
    return ""


def _iso_date(value: Any) -> str | None:
    text = str(value or "").strip()[:10]
    return text if _ISO_DATE.match(text) else None


def _location(record: dict[str, Any]) -> str:
    location = str(record.get("location") or "").strip()
    country = str(record.get("country") or "").strip()
    # "Belfast" says less than "Belfast, United Kingdom"; add the country when
    # the location does not already name it.
    if country and country.lower() not in location.lower():
        location = f"{location}, {country}" if location else country
    return location or "Not specified"


def _row(company: str, record: dict[str, Any]) -> tuple[Any, ...] | None:
    url = str(record.get("url") or "").strip()
    title = str(record.get("title") or "").strip()
    if not url or not title:
        return None
    # The URL, not the employer's reference, tells postings apart: one
    # requisition is often published once per office (NCC Group, Helsing).
    return (
        db.job_id_for(SOURCE_TYPE, company, url),
        url,
        title,
        company,
        _location(record),
        _first(record, _LEVEL_FIELDS),
        _first(record, _CATEGORY_FIELDS) or "General",
        _iso_date(_first(record, _POSTED_FIELDS)),
        _iso_date(record.get("deadline")),
        str(record.get("description") or ""),
        url,
    )


def company_for(site: str, document: dict[str, Any] | None = None) -> str:
    """How the site's employer is shown: what the extractor recorded, else
    the table above, else the folder name."""
    recorded = str((document or {}).get("company") or "").strip()
    return recorded or DISPLAY_NAMES.get(site, site)


def import_site(site: str, document: dict[str, Any]) -> SiteImport:
    """Mirror one site's latest.json into the jobs table."""
    return import_company(company_for(site, document), {site: document})


def import_company(company: str, documents: dict[str, dict[str, Any]]) -> SiteImport:
    """Mirror every latest.json of one employer into the jobs table.

    Two folders can name the same employer (Indra's whole board and its cyber
    search): a posting closes only when none of them lists it.
    """
    rows_by_id: dict[str, tuple[Any, ...]] = {}
    for document in documents.values():
        for record in document.get("postings") or []:
            row = _row(company, record)
            if row:
                rows_by_id.setdefault(row[0], row)
    rows = list(rows_by_id.values())
    ids = list(rows_by_id)
    with db.connect() as conn:
        known = {
            r["id"]
            for r in conn.execute(
                "SELECT id FROM jobs WHERE source_type = ? AND company = ?", (SOURCE_TYPE, company)
            )
        }
        conn.executemany(
            f"""
            INSERT INTO jobs (
                id, external_id, source_type, title, company, location, seniority,
                category, date_posted, deadline, description, apply_url, scored
            ) VALUES (?,?,'{SOURCE_TYPE}',?,?,?,?,?,?,?,?,?,0)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                location = excluded.location,
                seniority = excluded.seniority,
                category = excluded.category,
                date_posted = excluded.date_posted,
                deadline = excluded.deadline,
                description = excluded.description,
                apply_url = excluded.apply_url,
                closed_at = NULL,
                updated_at = datetime('now')
            """,
            rows,
        )
        closed = conn.execute(
            """
            UPDATE jobs SET closed_at = datetime('now'), updated_at = datetime('now')
            WHERE source_type = ? AND company = ? AND closed_at IS NULL
              AND id NOT IN (SELECT value FROM json_each(?))
            """,
            (SOURCE_TYPE, company, json.dumps(ids)),
        ).rowcount
    site = ", ".join(sorted(documents))
    return SiteImport(site, company, len(rows), len(set(ids) - known), closed)


def _site_files(root: Path) -> list[tuple[str, Path]]:
    if not root.is_dir():
        return []
    return sorted(
        (folder.name, folder / "latest.json")
        for folder in root.iterdir()
        if (folder / "latest.json").is_file()
    )


def sync(force: bool = False) -> list[SiteImport]:
    """Import every employer one of whose latest.json files changed since it
    was last imported. An employer's files are imported together, so that
    one of them cannot close what another still lists."""
    imported: list[SiteImport] = []
    with _lock:
        # company -> site -> (mtime stamp, parsed document)
        groups: dict[str, dict[str, tuple[str, dict[str, Any]]]] = {}
        unreadable: set[str] = set()
        for site, path in _site_files(paths.EXTRACTS_DIR):
            stamp = str(path.stat().st_mtime_ns)
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                logger.warning("extracts: could not read %s: %s", path, exc)
                # Importing the employer's other files alone would close
                # what this one lists: leave the employer as it is.
                unreadable.add(company_for(site))
                continue
            groups.setdefault(company_for(site, document), {})[site] = (stamp, document)
        for company, sites in groups.items():
            if company in unreadable:
                continue
            with db.connect() as conn:
                seen = {}
                for site in sites:
                    row = conn.execute(
                        "SELECT value FROM meta WHERE key = ?", (f"extract_mtime:{site}",)
                    ).fetchone()
                    seen[site] = row["value"] if row else None
                # A purge empties the jobs table but not meta: re-import then.
                present = conn.execute(
                    "SELECT 1 FROM jobs WHERE source_type = ? AND company = ? LIMIT 1",
                    (SOURCE_TYPE, company),
                ).fetchone()
            unchanged = all(seen[site] == stamp for site, (stamp, _) in sites.items())
            listed = any(doc.get("postings") for _, doc in sites.values())
            if not force and unchanged and (present or not listed):
                continue
            result = import_company(company, {site: doc for site, (_, doc) in sites.items()})
            with db.connect() as conn:
                conn.executemany(
                    "INSERT INTO meta (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    [(f"extract_mtime:{site}", stamp) for site, (stamp, _) in sites.items()],
                )
            logger.info(
                "extracts: %s — %d postings, %d new, %d closed",
                result.company,
                result.postings,
                result.new,
                result.closed,
            )
            imported.append(result)
    return imported


def sync_quietly() -> None:
    """sync(), for callers that must not fail because of it (page loads)."""
    try:
        sync()
    except Exception:  # noqa: BLE001 — a bad file must not take the grid down
        logger.exception("extracts: import failed")

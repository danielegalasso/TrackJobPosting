"""Every platform a board in boards.json can name, as one table.

Each entry takes the board's entry and returns a Collected. The platforms
built first (Workday, SuccessFactors, Phenom, Taleo, Pinpoint) each have a
module with its own entry scripts; the adapters here drive those modules
from a board entry instead, so one module serves every site on its platform.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from extract_common import DETAIL_WORKERS, Collected

_NOWHERE = Path("/nonexistent")


def workday(cfg: dict[str, Any]) -> Collected:
    """`host`, `tenant`, `site`; optional `query`, `facets` ({"locations": [...]}), `locale`."""
    from workday_extract import Board, build_record, fetch_detail, list_postings

    board = Board(
        name=cfg["name"],
        host=cfg["host"],
        tenant=cfg["tenant"],
        site=cfg["site"],
        locale=cfg.get("locale", "en-US"),
        default_query=cfg.get("query", ""),
        out_dir=_NOWHERE,
    )
    listing, total = list_postings(board, cfg.get("query", ""), cfg.get("facets"))
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        details = list(pool.map(lambda path: fetch_detail(board, path), listing))
    records = [build_record(board, path, entry, info) for (path, entry), info in zip(listing.items(), details)]
    # Workday states its total in the first page; a zero there is its own word.
    return Collected(records, total, sum(1 for info in details if not info), confirmed_empty=True)


def successfactors(cfg: dict[str, Any]) -> Collected:
    """The newer SuccessFactors career site: `host`, `locale`; optional `query`, `fields`."""
    from successfactors_extract import Site, build_record, fetch_detail, list_postings

    site = Site(
        name=cfg["name"],
        host=cfg["host"],
        locale=cfg.get("locale", "en_US"),
        default_query=cfg.get("query", ""),
        out_dir=_NOWHERE,
        fields=tuple(tuple(pair) for pair in cfg.get("fields", [])),
    )
    listing, total = list_postings(site, cfg.get("query", ""))
    urls = [site.job_url(str(entry.get("urlTitle") or ""), job_id) for job_id, entry in listing.items()]
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        details = list(pool.map(lambda url: fetch_detail(site, url), urls))
    records = [build_record(site, entry, detail) for entry, detail in zip(listing.values(), details)]
    return Collected(
        records, total, sum(1 for d in details if not d.get("_description")), confirmed_empty=True
    )


def phenom(cfg: dict[str, Any]) -> Collected:
    """`host`, `country`, `language`, `locale`; optional `query`."""
    from phenom_extract import Site, build_record, fetch_detail, list_postings

    site = Site(
        name=cfg["name"],
        host=cfg["host"],
        country=cfg.get("country", "global"),
        language=cfg.get("language", "en"),
        locale=cfg.get("locale", "en_global"),
        default_query=cfg.get("query", ""),
        out_dir=_NOWHERE,
    )
    listing, total = list_postings(site, cfg.get("query", ""))
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        details = list(pool.map(lambda seq: fetch_detail(site, seq), listing))
    records = [build_record(site, entry, job) for entry, job in zip(listing.values(), details)]
    return Collected(
        records, total, sum(1 for job in details if not job.get("description")), confirmed_empty=True
    )


def _load() -> dict[str, Callable[[dict[str, Any]], Collected]]:
    from ats_simple import PLATFORMS as simple

    table: dict[str, Callable[[dict[str, Any]], Collected]] = {
        "workday": workday,
        "successfactors": successfactors,
        "phenom": phenom,
        **simple,
    }
    for module in ("ats_enterprise", "sites_custom"):
        try:
            table.update(__import__(module).PLATFORMS)
        except ModuleNotFoundError as exc:
            if exc.name != module:
                raise
    return table


PLATFORMS = _load()

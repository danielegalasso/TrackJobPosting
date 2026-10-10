"""The per-site extractors' results, mirrored into the portal."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from acide import db, extracts, paths
from acide.models import RawPosting


def _posting(n: int, **fields) -> dict:
    record = {
        "job_id": f"R{n}",
        "title": f"Security Engineer {n}",
        "location": "Brussels",
        "country": "Belgium",
        "url": f"https://example.org/jobs/{n}",
        "description": f"Advert {n}",
    }
    record.update(fields)
    return record


def _write(root: Path, site: str, postings: list[dict], bump: int = 0, **head) -> Path:
    folder = root / site
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "latest.json"
    path.write_text(json.dumps({"query": "", **head, "postings": postings}), encoding="utf-8")
    # Make each write look changed to sync(), however fast the test runs.
    stamp = path.stat().st_mtime_ns + bump * 1_000_000_000
    os.utime(path, ns=(stamp, stamp))
    return path


@pytest.fixture
def extracts_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "extracts"
    root.mkdir()
    monkeypatch.setattr(paths, "EXTRACTS_DIR", root)
    return root


def _ids(**filters) -> list[str]:
    return [job.id for job in db.list_jobs(limit=200, **filters).items]


def test_sync_imports_postings_with_deadline_and_advert(extracts_dir):
    _write(extracts_dir, "NCCGroup", [_posting(1, deadline="2099-01-31", published="2026-09-01")])

    results = extracts.sync()

    assert [(r.company, r.postings, r.new, r.closed) for r in results] == [("NCC Group", 1, 1, 0)]
    job = db.list_jobs(origin="extract").items[0]
    assert job.company == "NCC Group"
    assert job.location == "Brussels, Belgium"
    assert job.deadline == "2099-01-31"
    assert job.date_posted == "2026-09-01"
    assert job.description == "Advert 1"
    assert job.apply_url == "https://example.org/jobs/1"
    assert job.scored is False
    assert job.closed is False


def test_unchanged_files_are_not_reread(extracts_dir):
    _write(extracts_dir, "NVISO", [_posting(1)])
    assert extracts.sync()
    assert extracts.sync() == []


def test_a_posting_no_longer_listed_is_closed_but_keeps_its_star(extracts_dir):
    _write(extracts_dir, "NVISO", [_posting(1), _posting(2)])
    extracts.sync()
    starred = db.job_id_for("extract", "NVISO", "https://example.org/jobs/2")
    db.set_job_flag(starred, "saved", True)

    _write(extracts_dir, "NVISO", [_posting(1)], bump=1)
    [result] = extracts.sync()

    assert result.closed == 1
    assert starred not in _ids()
    saved = db.list_jobs(saved_only=True).items
    assert [job.id for job in saved] == [starred]
    assert saved[0].closed is True


def test_reappearing_posting_reopens_and_keeps_operator_choices(extracts_dir):
    _write(extracts_dir, "NVISO", [_posting(1)])
    extracts.sync()
    job_id = db.job_id_for("extract", "NVISO", "https://example.org/jobs/1")
    db.set_job_flag(job_id, "dismissed", True)

    _write(extracts_dir, "NVISO", [], bump=1)
    extracts.sync()
    _write(extracts_dir, "NVISO", [_posting(1, title="Renamed")], bump=2)
    extracts.sync()

    job = db.get_job(job_id)
    assert job is not None
    assert job.closed is False
    assert job.dismissed is True
    assert job.title == "Renamed"
    assert job_id in _ids(dismissed_only=True)
    assert job_id not in _ids()


def test_purge_then_sync_imports_again(extracts_dir):
    _write(extracts_dir, "NVISO", [_posting(1)])
    extracts.sync()
    db.delete_all_jobs()

    assert [r.postings for r in extracts.sync()] == [1]


def test_deadline_sort_puts_soonest_first_and_passed_last(extracts_dir):
    today = date.today()
    _write(
        extracts_dir,
        "EDA",
        [
            _posting(1, deadline=(today + timedelta(days=30)).isoformat()),
            _posting(2, deadline=(today - timedelta(days=5)).isoformat()),
            _posting(3),
            _posting(4, deadline=(today + timedelta(days=2)).isoformat()),
        ],
    )
    extracts.sync()

    titles = [job.title for job in db.list_jobs(sort="deadline").items]
    assert titles == [
        "Security Engineer 4",
        "Security Engineer 1",
        "Security Engineer 3",
        "Security Engineer 2",
    ]


def test_recent_sort_puts_newest_publication_first(extracts_dir):
    _write(
        extracts_dir,
        "EDA",
        [_posting(1, published="2026-09-01"), _posting(2, published="2026-09-20"), _posting(3)],
    )
    extracts.sync()

    titles = [job.title for job in db.list_jobs(sort="recent").items]
    assert titles[:2] == ["Security Engineer 2", "Security Engineer 1"]


def test_origin_separates_extracts_from_the_crawler(extracts_dir):
    _write(extracts_dir, "NVISO", [_posting(1)])
    extracts.sync()
    db.store_posting(
        RawPosting(
            external_id="x1",
            company="Crawled Co",
            title="Crawled role",
            location="Paris",
            apply_url="https://crawled.example/1",
            source_type="greenhouse",
        )
    )

    assert {job.company for job in db.list_jobs(origin="extract").items} == {"NVISO"}
    assert {job.company for job in db.list_jobs(origin="crawler").items} == {"Crawled Co"}
    assert len(db.list_jobs().items) == 2
    assert db.facets("extract").companies == ["NVISO"]


def test_records_without_a_url_or_title_are_skipped(extracts_dir):
    _write(extracts_dir, "NVISO", [_posting(1), _posting(2, url=""), _posting(3, title="")])
    [result] = extracts.sync()
    assert result.postings == 1


def test_an_unreadable_file_does_not_stop_the_others(extracts_dir):
    (extracts_dir / "Broken").mkdir()
    (extracts_dir / "Broken" / "latest.json").write_text("{not json", encoding="utf-8")
    _write(extracts_dir, "NVISO", [_posting(1)])

    assert [r.company for r in extracts.sync()] == ["NVISO"]


def test_api_lists_extracts_after_a_file_changes(client, extracts_dir):
    _write(extracts_dir, "CERTEU", [_posting(1, deadline="2099-10-10")])

    response = client.get("/api/jobs", params={"origin": "extract", "sort": "deadline"})

    assert response.status_code == 200
    [item] = response.json()["items"]
    assert item["company"] == "CERT-EU"
    assert item["deadline"] == "2099-10-10"
    assert item["scored"] is False


def test_api_rejects_an_unknown_origin(client):
    assert client.get("/api/jobs", params={"origin": "elsewhere"}).status_code == 422


def test_api_treats_an_empty_origin_as_every_source(client):
    assert client.get("/api/jobs", params={"origin": ""}).status_code == 200
    assert client.get("/api/jobs/facets", params={"origin": ""}).status_code == 200


def test_a_database_from_before_these_columns_is_migrated(tmp_path, monkeypatch):
    old = tmp_path / "old.db"
    # The schema as it was before deadline / description / closed_at.
    previous = "\n".join(
        line
        for line in db._SCHEMA.splitlines()
        if not line.strip().startswith(("deadline ", "description ", "closed_at "))
    )
    conn = sqlite3.connect(old)
    conn.executescript(previous)
    conn.execute(
        "INSERT INTO jobs (id, external_id, title, company, apply_url) "
        "VALUES ('a', 'a', 'Old role', 'Old Co', 'https://old.example')"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(paths, "DB_PATH", old)
    db.reset_connection()

    db.init_db()

    with db.connect() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    assert {"deadline", "description", "closed_at"} <= columns
    # The old posting is still there, live, and listed.
    assert [job.title for job in db.list_jobs().items] == ["Old role"]


def test_the_company_recorded_by_the_extractor_is_shown(extracts_dir):
    path = extracts_dir / "Wiz" / "latest.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"company": "Wiz, Inc.", "postings": [_posting(1)]}), encoding="utf-8")

    [result] = extracts.sync()

    assert result.company == "Wiz, Inc."
    assert db.list_jobs(origin="extract").items[0].company == "Wiz, Inc."


def test_two_folders_of_one_employer_do_not_close_each_other(extracts_dir):
    # Indra's whole board and its cyber search, which lists a subset of it.
    _write(extracts_dir, "IndraAll", [_posting(1), _posting(2)], company="Indra")
    _write(extracts_dir, "IndraCyber", [_posting(2)], company="Indra")

    [result] = extracts.sync()

    assert (result.company, result.postings, result.closed) == ("Indra", 2, 0)
    assert len(_ids(origin="extract")) == 2

    # Only the cyber search changed, and dropped its posting: the whole board
    # still lists it, so it stays open.
    _write(extracts_dir, "IndraCyber", [], bump=1, company="Indra")
    [result] = extracts.sync()

    assert (result.postings, result.closed) == (2, 0)
    assert len(_ids(origin="extract")) == 2

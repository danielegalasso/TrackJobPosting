"""Run the Search: the extractors, the import, and the review, from the API."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from acide import db, paths, ranking, search

FAKE_RUN_ALL = '''
import json, sys
from pathlib import Path
here = Path(__file__).parent
if "--list" in sys.argv:
    print("Acme")
    sys.exit(0)
postings = [
    {"title": "Cyber Threat Intelligence Analyst", "url": "https://acme.example/1",
     "location": "Brussels, Belgium", "description": "Threat intelligence."},
    {"title": "Security Engineer", "url": "https://acme.example/2",
     "location": "Austin, TX", "description": "Detection."},
]
(here / "Acme").mkdir(exist_ok=True)
(here / "Acme" / "latest.json").write_text(json.dumps({"company": "Acme", "postings": postings}))
print("Acme           OK                    2/2  +2 -0          1s", flush=True)
'''


@pytest.fixture
def extractors(isolated_state: Path) -> Path:
    folder = paths.EXTRACTS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "run_all.py").write_text(FAKE_RUN_ALL, encoding="utf-8")
    return folder


def _wait(predicate, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("timed out")


def test_the_brief_is_read_and_replaced(client):
    assert client.get("/api/search/brief").json()["text"] == ""
    saved = client.put("/api/search/brief", json={"text": "I am a security engineer."}).json()
    body = client.get("/api/search/brief").json()
    assert body["text"] == "I am a security engineer."
    assert body["digest"] == saved["digest"] == ranking.brief_digest("I am a security engineer.")
    assert "ONE job offer" in body["instructions"]
    assert client.put("/api/search/brief", json={"text": ""}).status_code == 422


def test_the_scoring_settings_are_saved(client):
    body = client.put(
        "/api/search/scoring",
        json={"model": "z-ai/glm-5.3-flash", "reasoning_effort": "xhigh", "max_concurrency": 8},
    ).json()
    assert (body["model"], body["reasoning_effort"], body["max_concurrency"]) == (
        "z-ai/glm-5.3-flash",
        "xhigh",
        8,
    )
    bad = {"model": "x", "reasoning_effort": "huge", "max_concurrency": 8}
    assert client.put("/api/search/scoring", json=bad).status_code == 422


def test_the_estimate_counts_and_prices_from_history(client):
    client.put("/api/search/brief", json={"text": "Brief."})
    with db.connect() as conn:
        for n in range(6):
            conn.execute(
                """INSERT INTO jobs (id, external_id, source_type, title, company, location, apply_url,
                                     priority_llm)
                   VALUES (?, ?, 'extract', 'Security Analyst', 'Thales', 'Paris, France', ?, ?)""",
                (
                    f"j{n}",
                    f"j{n}",
                    f"https://x/{n}",
                    json.dumps({"model": "google/gemini-2.5-flash", "cost": 0.001, "brief": "old"}),
                ),
            )
    ranking.refresh()
    body = client.get("/api/search/estimate", params={"mode": "stale"}).json()
    assert body["count"] == 6
    assert body["source"] == "history" and body["per_posting"] == pytest.approx(0.001)
    assert body["total"] == pytest.approx(0.006)
    assert client.get("/api/search/estimate", params={"mode": "new"}).json()["count"] == 0


def test_a_run_imports_what_is_new_and_says_where(client, extractors, monkeypatch):
    reviewed = []
    monkeypatch.setattr(search, "_review_new", lambda: reviewed.append(True))

    assert client.post("/api/search/run").json() == {"started": True}
    _wait(lambda: not search.is_running())

    state = client.get("/api/search/status").json()["search"]
    assert state["phase"] == "done", state["error"]
    assert state["sites_total"] == 1
    assert state["sites"][0]["name"] == "Acme" and state["sites"][0]["new"] == 2
    assert state["changes"] == [{"company": "Acme", "new": 2, "gone": 0, "new_excluded": 1}]
    assert reviewed == [True]
    # Texas needs a visa: imported, ranked, and left out of the search.
    titles = [item["title"] for item in client.get("/api/jobs").json()["items"]]
    assert titles == ["Cyber Threat Intelligence Analyst"]


def test_a_posting_no_longer_listed_leaves_the_search(client, extractors, monkeypatch):
    monkeypatch.setattr(search, "_review_new", lambda: None)
    client.post("/api/search/run")
    _wait(lambda: not search.is_running())

    script = (extractors / "run_all.py").read_text()
    (extractors / "run_all.py").write_text(
        script.replace('"url": "https://acme.example/2"', '"url": "https://acme.example/3"')
    )
    # The table's clock has a one-second grain: start the second run after it.
    time.sleep(1.1)
    client.post("/api/search/run")
    _wait(lambda: not search.is_running())

    state = search.state
    assert [(c.company, c.new, c.gone) for c in state.changes] == [("Acme", 1, 1)]


def test_a_second_run_is_refused_while_one_is_going(client, extractors, monkeypatch):
    monkeypatch.setattr(search, "_review_new", lambda: time.sleep(0.5))
    assert client.post("/api/search/run").status_code == 200
    assert client.post("/api/search/run").status_code == 409
    _wait(lambda: not search.is_running())


def test_a_review_needs_a_brief_and_a_key(client):
    assert client.post("/api/search/review", json={"mode": "stale"}).status_code == 409
    client.put("/api/search/brief", json={"text": "Brief."})
    assert client.post("/api/search/review", json={"mode": "stale"}).status_code == 409

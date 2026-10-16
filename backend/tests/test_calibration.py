"""Learning from stars and removals, and the review prompt's cache."""

from __future__ import annotations

import json
import threading

import pytest
from test_ranking import FakeClient, _brief, _insert, _review_payload

from acide import calibration, db, ranking


class CalibrationClient:
    def __init__(self, guidelines: list[str] | None = None) -> None:
        self.calls: list[dict] = []
        self.guidelines = guidelines if guidelines is not None else ["Abbassa i ruoli GRC di 15 punti."]

    def complete_raw(self, messages, **overrides):
        self.calls.append({"messages": messages, **overrides})
        answer = {"guidelines": self.guidelines, "notes": "Meno GRC."}
        return {"choices": [{"message": {"content": json.dumps(answer)}}], "usage": {"cost": 0.003}}

    def close(self) -> None:
        pass


def _reviewed(job_id: str, title: str, score: int, reason: str = "Buon ruolo.") -> None:
    _insert(job_id, title, "ENISA")
    ranking.store_review(job_id, {"score": score, "justification": reason, "brief": "x"})


def test_a_star_or_removal_is_logged_once_per_change():
    _insert("a", "Security Analyst", "ENISA")
    db.set_job_flag("a", "saved", True)
    db.set_job_flag("a", "saved", True)  # already starred: nothing new
    db.set_job_flag("a", "dismissed", None)
    db.set_job_flag("a", "saved", False)
    with db.connect() as conn:
        events = [tuple(r) for r in conn.execute("SELECT job_id, kind, value FROM feedback_events ORDER BY id")]
    assert events == [("a", "saved", 1), ("a", "dismissed", 1), ("a", "saved", 0)]
    # Undoing is logged, but only a star or a removal counts towards learning.
    assert calibration.pending() == 2


def test_calibrating_reads_the_feedback_and_writes_the_prompt_section():
    _brief()
    _reviewed("a", "Malware Researcher", 35, "Ruolo poco chiaro.")
    _reviewed("b", "GRC Analyst", 82)
    _reviewed("c", "SOC Analyst", 30)
    db.set_job_flag("a", "saved", True)
    db.set_job_flag("b", "dismissed", True)
    db.set_job_flag("c", "dismissed", True)
    client = CalibrationClient()

    current = calibration.calibrate(client=client, say=lambda _: None)

    sent = client.calls[0]["messages"]
    assert "Sono un ingegnere della sicurezza." in sent[0]["content"]
    assert "★ score 35 · ENISA — Malware Researcher" in sent[1]["content"]
    assert "model's reason: Ruolo poco chiaro." in sent[1]["content"]
    assert "✕ score 82 · ENISA — GRC Analyst" in sent[1]["content"]
    assert current["version"] == 1 and current["cost"] == pytest.approx(0.003)
    # The examples are where the model was most wrong; a removal it already
    # scored low (the SOC role, 30) taught it nothing.
    assert [(e["action"], e["title"]) for e in current["examples"]] == [
        ("starred", "Malware Researcher"),
        ("removed", "GRC Analyst"),
    ]
    section = calibration.prompt_section()
    assert "- Abbassa i ruoli GRC di 15 punti." in section
    assert "- ★ starred, you gave 35: ENISA — Malware Researcher (Brussels, Belgium)" in section
    assert calibration.pending() == 0 and calibration.version() == 1

    # The next one starts from these guidelines, and the old one is kept.
    calibration.calibrate(client=client, say=lambda _: None)
    assert "- Abbassa i ruoli GRC di 15 punti." in client.calls[1]["messages"][1]["content"]
    assert calibration.version() == 2
    history = calibration._history_path().read_text("utf-8").splitlines()
    assert json.loads(history[0])["version"] == 1


def test_it_learns_by_itself_every_so_many_stars_and_removals(monkeypatch):
    _brief()
    from acide import config as config_module

    settings = config_module.load()
    settings.openrouter.api_key = "sk-test"
    config_module.save(settings)
    calibration.save_settings(every=5)
    started = []
    monkeypatch.setattr(calibration, "start", lambda say=None: started.append(True) or True)
    for index in range(5):
        _insert(str(index), "Security Analyst", "ENISA")
        assert not started
        calibration.maybe_start()
        db.set_job_flag(str(index), "dismissed", True)
    assert calibration.maybe_start() and len(started) == 1

    calibration.save_settings(auto=False)
    assert not calibration.maybe_start()


def test_resetting_forgets_the_guidelines_and_the_feedback_so_far():
    _brief()
    _reviewed("a", "Malware Researcher", 35)
    db.set_job_flag("a", "saved", True)
    calibration.calibrate(client=CalibrationClient(), say=lambda _: None)
    db.set_job_flag("a", "dismissed", True)
    assert calibration.pending() == 1
    calibration.reset()
    assert calibration.prompt_section() == "" and calibration.version() == 0
    assert calibration.pending() == 0


def test_the_review_carries_the_calibration_and_keeps_its_prefix_cached():
    _brief()
    _reviewed("x", "Malware Researcher", 35)
    db.set_job_flag("x", "saved", True)
    calibration.calibrate(client=CalibrationClient(), say=lambda _: None)
    for job_id in "abc":
        _insert(job_id, "Security Analyst", "ENISA")

    order: list[str] = []
    lock = threading.Lock()

    class CachingClient(FakeClient):
        def complete_raw(self, messages, **overrides):
            with lock:
                first = not self.calls
                self.calls.append({"messages": messages, **overrides})
                order.append("first" if first else "rest")
            payload = _review_payload()
            payload["usage"].update(
                prompt_tokens=3000, prompt_tokens_details={"cached_tokens": 0 if first else 2500}
            )
            return payload

    client = CachingClient()
    run = ranking.review_top(3, mode="new", client=client, say=lambda _: None)

    assert run.reviewed == 3 and run.calibration == 1
    systems = {call["messages"][0]["content"] for call in client.calls}
    assert len(systems) == 1  # byte-identical, or the cache is never read
    system = systems.pop()
    assert system.index("Sono un ingegnere") < system.index("## Calibration") < system.index("## This request")
    sessions = {call["session_id"] for call in client.calls}
    assert len(sessions) == 1 and sessions.pop().endswith("-c1")
    assert (run.prompt_tokens, run.cached_tokens) == (9000, 5000)
    stored = json.loads(_raw_review("a"))
    assert stored["calibration"] == 1 and "cached" in stored["tokens"]

    # Reviews made before a new calibration are counted, and rescored on request.
    calibration.calibrate(client=CalibrationClient(["Altro."]), say=lambda _: None)
    digest = ranking.brief_digest(ranking.load_brief())
    assert ranking.review_counts(digest)["older_calibration"] == 3
    assert ranking.review_top(None, mode="calibration", client=FakeClient(), say=lambda _: None).reviewed == 3
    assert ranking.review_counts(digest)["older_calibration"] == 0


def _raw_review(job_id: str) -> str:
    with db.connect() as conn:
        return conn.execute("SELECT priority_llm FROM jobs WHERE id = ?", (job_id,)).fetchone()[0]


def test_the_api_runs_and_resets_the_calibration(client, monkeypatch):
    status = client.get("/api/search/calibration").json()
    assert status["settings"] == {"auto": True, "every": calibration.DEFAULT_EVERY}
    assert status["current"] is None and status["pending"] == 0

    response = client.put("/api/search/calibration", json={"auto": False, "every": 20})
    assert response.json()["settings"] == {"auto": False, "every": 20}
    assert client.post("/api/search/calibration/run").status_code == 409  # no brief yet
    assert client.delete("/api/search/calibration").status_code == 200

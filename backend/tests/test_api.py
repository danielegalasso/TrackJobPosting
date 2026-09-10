"""HTTP surface: the contract the portal front-end depends on."""

from __future__ import annotations

from test_filters import seed

from acide import db


def test_health_reports_configuration_state(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["openrouter_configured"] is False


def test_jobs_endpoint_paginates_and_filters(client):
    for index in range(3):
        seed(external_id=str(index), title=f"Role {index}", company="Acme")
    seed(external_id="9", title="Other", company="Globex")

    page = client.get("/api/jobs", params={"limit": 2}).json()
    assert page["total"] == 4 and len(page["items"]) == 2 and page["has_more"] is True

    filtered = client.get("/api/jobs", params={"company": "Globex"}).json()
    assert [item["title"] for item in filtered["items"]] == ["Other"]


def test_jobs_endpoint_rejects_bad_enum_values(client):
    assert client.get("/api/jobs", params={"posted_within": "yesterday"}).status_code == 422
    assert client.get("/api/jobs", params={"sort": "random"}).status_code == 422
    assert client.get("/api/jobs", params={"min_amount": -5}).status_code == 422


def test_facets_route_is_not_shadowed_by_the_job_detail_route(client):
    seed(external_id="1", category="Cloud Security")
    body = client.get("/api/jobs/facets").json()
    assert body["categories"] == ["Cloud Security"]


def test_bookmark_toggles_and_persists(client):
    job_id = seed(external_id="1", title="Saved Me")

    toggled = client.post(f"/api/jobs/{job_id}/bookmark").json()
    assert toggled["saved"] is True
    assert client.post(f"/api/jobs/{job_id}/bookmark").json()["saved"] is False
    assert client.post(f"/api/jobs/{job_id}/bookmark", params={"saved": True}).json()["saved"] is True

    saved = client.get("/api/jobs", params={"saved_only": True}).json()
    assert [item["title"] for item in saved["items"]] == ["Saved Me"]


def test_dismiss_hides_a_job_from_the_grid(client):
    job_id = seed(external_id="1", title="Not For Me")
    client.post(f"/api/jobs/{job_id}/dismiss")
    assert client.get("/api/jobs").json()["total"] == 0
    assert client.get("/api/jobs", params={"include_dismissed": True}).json()["total"] == 1


def test_unknown_job_returns_404(client):
    assert client.get("/api/jobs/nope:123").status_code == 404
    assert client.post("/api/jobs/nope:123/bookmark").status_code == 404


def test_alert_subscription_lifecycle(client):
    created = client.post(
        "/api/alerts",
        json={"email": "candidate@example.com", "filters": {"search": "embedded", "min_amount": 150000}},
    )
    assert created.status_code == 201
    alert_id = created.json()["id"]
    assert created.json()["filters"]["search"] == "embedded"

    assert client.post(f"/api/alerts/{alert_id}/pause").json()["active"] is False
    assert client.post(f"/api/alerts/{alert_id}/resume").json()["active"] is True

    assert client.delete(f"/api/alerts/{alert_id}").status_code == 204
    assert client.get("/api/alerts").json() == []


def test_alert_rejects_a_malformed_email(client):
    response = client.post("/api/alerts", json={"email": "not-an-email", "filters": {}})
    assert response.status_code == 422


def test_unsubscribe_link_works_without_authentication(client):
    alert_id = client.post(
        "/api/alerts", json={"email": "candidate@example.com", "filters": {}}
    ).json()["id"]

    page = client.get(f"/api/alerts/{alert_id}/unsubscribe")
    assert page.status_code == 200
    assert "unsubscribed" in page.text.lower()
    assert db.list_alerts() == []

    # A second click must not error.
    assert client.get(f"/api/alerts/{alert_id}/unsubscribe").status_code == 200


def test_config_masks_secrets_and_preserves_them_on_save(client):
    payload = {
        "openrouter": {"api_key": "sk-real-secret", "model": "deepseek/deepseek-chat"},
        "email": {"enabled": True, "smtp_server": "smtp.example.com",
                  "sender_email": "a@b.c", "sender_password": "hunter2"},
        "interests": ["Cloud security"],
        "targets": [{"company": "Acme", "source_type": "greenhouse", "board_token": "acme"}],
    }
    saved = client.put("/api/config", json=payload).json()
    assert saved["openrouter"]["api_key"] == "••••••••"
    assert saved["email"]["sender_password"] == "••••••••"

    # Saving the masked form back must not wipe the stored secrets.
    resaved = client.put("/api/config", json=saved)
    assert resaved.status_code == 200

    from acide import config as config_module

    config_module.reset_cache()
    stored = config_module.load(refresh=True)
    assert stored.openrouter.api_key == "sk-real-secret"
    assert stored.email.sender_password == "hunter2"
    assert stored.openrouter.model == "deepseek/deepseek-chat"


def test_config_rejects_an_unknown_source_type(client):
    response = client.put(
        "/api/config",
        json={"targets": [{"company": "X", "source_type": "linkedin", "board_token": "x"}]},
    )
    assert response.status_code == 422


def test_config_advertises_available_source_types(client):
    body = client.get("/api/config").json()
    assert set(body["source_types"]) == {"greenhouse", "lever", "ashby"}


def test_resume_upload_extract_and_erase(client):
    response = client.post(
        "/api/config/resume",
        files={"file": ("cv.md", b"# Daniele\n\nTen years of embedded C and Python.", "text/markdown")},
    )
    assert response.status_code == 200
    assert response.json()["filename"] == "cv.md"
    assert "embedded C" in response.json()["preview"]
    assert client.get("/api/config").json()["resume_filename"] == "cv.md"

    assert client.delete("/api/config/resume").status_code == 204
    assert client.get("/api/config").json()["resume_filename"] == ""


def test_resume_upload_rejects_an_unsupported_format(client):
    response = client.post(
        "/api/config/resume",
        files={"file": ("cv.docx", b"binary", "application/octet-stream")},
    )
    assert response.status_code == 400


def test_resume_upload_cannot_escape_the_data_directory(client, isolated_state):
    """An uploaded filename is untrusted input."""
    response = client.post(
        "/api/config/resume",
        files={"file": ("../../evil.md", b"payload", "text/markdown")},
    )
    assert response.status_code == 200
    assert not (isolated_state.parent / "evil.md").exists()
    assert (isolated_state / "resume" / "evil.md").exists()


def test_spider_status_and_manual_trigger(client):
    status = client.get("/api/spider/status").json()
    assert status["running"] is False
    assert set(status["targets"]) == set()

    started = client.post("/api/spider/run", params={"send_alerts": False}).json()
    assert started["started"] is True


def test_spider_log_history_is_readable(client):
    from acide.logbus import bus

    bus.publish("test line", "info")
    history = client.get("/api/spider/logs/history").json()
    assert any(entry["message"] == "test line" for entry in history)


def test_legal_documents_are_served(client):
    for document in ("privacy", "terms"):
        response = client.get(f"/api/legal/{document}")
        assert response.status_code == 200
        assert len(response.text) > 500
    assert client.get("/api/legal/nonsense").status_code == 404


def test_unknown_api_path_is_a_404_not_the_spa(client):
    assert client.get("/api/does-not-exist").status_code == 404


def test_purge_jobs_clears_the_index(client):
    seed(external_id="1")
    assert client.post("/api/config/purge-jobs").json()["deleted"] == 1
    assert client.get("/api/jobs").json()["total"] == 0


def test_single_page_app_is_served_when_built(tmp_path, monkeypatch):
    """The SPA catch-all only registers once a build exists.

    Regression guard: the route is added at import time inside
    `_mount_frontend`, so a test run against a repo with no `dist/` never
    exercises it. This builds a stand-in dist and mounts a fresh app.
    """
    import importlib

    from fastapi.testclient import TestClient

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>ACIDE-Watch</title>")
    (dist / "assets" / "app.js").write_text("console.log('acide');")

    from acide import main as main_module
    from acide import paths

    monkeypatch.setattr(paths, "FRONTEND_DIST", dist)
    reloaded = importlib.reload(main_module)

    with TestClient(reloaded.app) as spa_client:
        index = spa_client.get("/")
        assert index.status_code == 200
        assert "ACIDE-Watch" in index.text

        # A client-side route falls through to index.html…
        assert spa_client.get("/settings").status_code == 200
        # …a real asset is served as itself…
        assert "acide" in spa_client.get("/assets/app.js").text
        # …and an unknown API path stays a JSON 404.
        assert spa_client.get("/api/nope").status_code == 404
        # The API still answers through the catch-all app.
        assert spa_client.get("/api/health").json()["status"] == "ok"

    # Restore the module for any test that runs after this one.
    monkeypatch.undo()
    importlib.reload(main_module)

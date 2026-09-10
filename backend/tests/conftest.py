"""Test fixtures: every test gets an isolated data directory."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point paths, config and DB at a throwaway directory per test."""
    from acide import config as config_module
    from acide import db, paths

    data_dir = tmp_path / "data"
    monkeypatch.setattr(paths, "DATA_DIR", data_dir)
    monkeypatch.setattr(paths, "CONFIG_PATH", data_dir / "setup.json")
    monkeypatch.setattr(paths, "DB_PATH", data_dir / "acide_storage.db")
    monkeypatch.setattr(paths, "RESUME_DIR", data_dir / "resume")

    db.reset_connection()
    config_module.reset_cache()
    paths.ensure_dirs()
    db.init_db()
    yield data_dir
    db.reset_connection()
    config_module.reset_cache()


@pytest.fixture
def client(isolated_state):
    """A TestClient bound to the app, sharing the isolated state."""
    from fastapi.testclient import TestClient

    from acide.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def fixture_dir() -> Path:
    return Path(__file__).parent / "fixtures"


def load_fixture(fixture_dir: Path, name: str):
    import json

    return json.loads((fixture_dir / name).read_text("utf-8"))

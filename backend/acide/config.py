"""Load, validate and persist `setup.json`.

Secrets never leave the host in a GET response: `redacted()` masks the
OpenRouter key and SMTP password, and `merge_secrets()` lets the settings
form save without the browser ever having to echo them back.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
from typing import Any

from . import paths
from .models import SECRET_FIELDS, SetupConfig

MASK = "••••••••"
_lock = threading.Lock()
_cache: SetupConfig | None = None


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    """Let environment variables win over the file, for container deploys."""
    overrides = (
        ("ACIDE_OPENROUTER_API_KEY", ("openrouter", "api_key")),
        ("ACIDE_OPENROUTER_MODEL", ("openrouter", "model")),
        ("ACIDE_SMTP_SERVER", ("email", "smtp_server")),
        ("ACIDE_SMTP_PASSWORD", ("email", "sender_password")),
        ("ACIDE_SENDER_EMAIL", ("email", "sender_email")),
        ("ACIDE_ADMIN_EMAIL", ("admin_email",)),
    )
    for env_name, path in overrides:
        value = os.environ.get(env_name)
        if not value:
            continue
        cursor = data
        for key in path[:-1]:
            cursor = cursor.setdefault(key, {})
        cursor[path[-1]] = value
    return data


def load(refresh: bool = False) -> SetupConfig:
    """Return the active configuration, creating a blank one on first run."""
    global _cache
    with _lock:
        if _cache is not None and not refresh:
            return _cache
        raw: dict[str, Any] = {}
        if paths.CONFIG_PATH.exists():
            try:
                raw = json.loads(paths.CONFIG_PATH.read_text("utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{paths.CONFIG_PATH} is not valid JSON: {exc}") from exc
        _cache = SetupConfig(**_apply_env_overrides(raw))
        return _cache


def save(config: SetupConfig) -> SetupConfig:
    """Persist configuration with owner-only permissions."""
    global _cache
    with _lock:
        paths.ensure_dirs()
        payload = config.model_dump_json(indent=2)
        tmp = paths.CONFIG_PATH.with_suffix(".json.tmp")
        tmp.write_text(payload, encoding="utf-8")
        with contextlib.suppress(OSError):
            tmp.chmod(0o600)
        tmp.replace(paths.CONFIG_PATH)
        _cache = config
        return config


def redacted(config: SetupConfig | None = None) -> dict[str, Any]:
    """Config for the settings screen, with every secret masked."""
    config = config or load()
    data = config.model_dump()
    for section, field in SECRET_FIELDS:
        if data.get(section, {}).get(field):
            data[section][field] = MASK
    return data


def merge_secrets(incoming: dict[str, Any], current: SetupConfig | None = None) -> dict[str, Any]:
    """Restore stored secrets wherever the client sent back the mask."""
    current = current or load()
    stored = current.model_dump()
    for section, field in SECRET_FIELDS:
        value = incoming.get(section, {}).get(field)
        if value in (MASK, "", None):
            incoming.setdefault(section, {})[field] = stored.get(section, {}).get(field, "")
    return incoming


def reset_cache() -> None:
    """Drop the in-process cache (used by tests and by config reloads)."""
    global _cache
    with _lock:
        _cache = None

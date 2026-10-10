"""Filesystem layout for ACIDE-Watch runtime state.

Everything the engine writes lives under a single data directory so a
self-hosted operator can back it up -- or wipe it, exercising the erasure
right described in the Privacy Policy -- with one command.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

# Repository root: backend/acide/paths.py -> backend/acide -> backend -> root
REPO_ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = Path(os.environ.get("ACIDE_DATA_DIR", REPO_ROOT / "data")).resolve()
CONFIG_PATH = Path(os.environ.get("ACIDE_CONFIG_PATH", DATA_DIR / "setup.json"))
DB_PATH = Path(os.environ.get("ACIDE_DB_PATH", DATA_DIR / "acide_storage.db"))
RESUME_DIR = DATA_DIR / "resume"
# The operator's own words on who they are and how to rank postings: what the
# priority review sends to the model. Personal, so it lives with the data.
RANKING_BRIEF_PATH = DATA_DIR / "ranking-brief.md"

# Built single-page app, served by the API process when present.
FRONTEND_DIST = Path(
    os.environ.get("ACIDE_FRONTEND_DIST", REPO_ROOT / "frontend" / "dist")
)

EXAMPLE_CONFIG_PATH = REPO_ROOT / "setup.example.json"

# Where the per-site extractors write their results, one folder per site with
# a latest.json in it (ExtractFromTheCarrerPageTheJobs/run_all.py).
EXTRACTS_DIR = Path(
    os.environ.get("ACIDE_EXTRACTS_DIR", REPO_ROOT / "ExtractFromTheCarrerPageTheJobs")
)


def ensure_dirs() -> None:
    """Create the runtime directories, with owner-only permissions.

    The data directory holds the OpenRouter key, the SMTP password and the
    candidate's resume text, so it is never world-readable.
    """
    for directory in (DATA_DIR, RESUME_DIR):
        directory.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):  # exotic filesystems
            directory.chmod(0o700)

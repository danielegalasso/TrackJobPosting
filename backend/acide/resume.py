"""Curriculum Vitae ingestion.

Accepts `.pdf`, `.md` and `.txt`, extracts plain text once, and keeps a
single active CV on disk. Replacing the CV purges the previous file, which
is the behaviour the Privacy Policy commits to.
"""

from __future__ import annotations

import contextlib
import re
from pathlib import Path

from . import paths

SUPPORTED_SUFFIXES = {".pdf", ".md", ".markdown", ".txt"}
MAX_BYTES = 8 * 1024 * 1024


class ResumeError(ValueError):
    """Raised when an upload cannot be accepted or read."""


def _extract_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise ResumeError("pypdf is required to read PDF resumes") from exc

    reader = PdfReader(str(path))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages)


def extract_text(path: Path) -> str:
    """Read a CV file into normalised plain text."""
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ResumeError(f"unsupported resume format: {suffix or 'unknown'}")
    text = _extract_pdf(path) if suffix == ".pdf" else path.read_text("utf-8", errors="replace")
    return normalise(text)


def normalise(text: str) -> str:
    """Collapse the whitespace PDF extraction leaves behind."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def store(filename: str, payload: bytes) -> Path:
    """Save an uploaded CV, replacing any previous one."""
    if len(payload) > MAX_BYTES:
        raise ResumeError("resume exceeds the 8 MB limit")
    # Take only the basename: an uploaded name is untrusted input and must
    # never be able to escape the resume directory.
    safe_name = Path(filename or "cv").name
    suffix = Path(safe_name).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ResumeError(f"unsupported resume format: {suffix or 'unknown'}")

    paths.ensure_dirs()
    purge()
    destination = paths.RESUME_DIR / safe_name
    destination.write_bytes(payload)
    with contextlib.suppress(OSError):
        destination.chmod(0o600)
    return destination


def purge() -> None:
    """Delete every stored CV file."""
    if not paths.RESUME_DIR.exists():
        return
    for existing in paths.RESUME_DIR.iterdir():
        if existing.is_file():
            existing.unlink()


def active_path() -> Path | None:
    if not paths.RESUME_DIR.exists():
        return None
    files = sorted(p for p in paths.RESUME_DIR.iterdir() if p.is_file())
    return files[0] if files else None


def active_text() -> str:
    """Plain text of the stored CV, or an empty string when none is set."""
    path = active_path()
    if path is None:
        return ""
    try:
        return extract_text(path)
    except ResumeError:
        return ""

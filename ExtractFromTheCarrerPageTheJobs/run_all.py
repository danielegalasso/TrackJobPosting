#!/usr/bin/env python3
"""Run every extractor here, one after another, and summarise.

That is every extract*Positions.py script, and every board in boards.json
(run through extract_boards.py).

Each extractor is run as its own process, so one site failing (a timeout, a
page that changed shape, a crash) does not stop the others. Their output is
kept in logs/run_<timestamp>.log; the summary shows, per site, what it found
and whether the run can be trusted:

    OK          every posting the site reports was extracted
    CHECK       complete, but more than half of last run's postings are gone
    INCOMPLETE  fewer postings than the site reports (latest.csv kept as before)
    EMPTY       nothing found; the site has none or no longer reads as expected
    FAILED      the extractor stopped with an error, or timed out

The exit status is 0 only when every site is OK or CHECK.

Usage:  python3 run_all.py [--jobs N] [site ...]     e.g.  python3 run_all.py Airbus NATO
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG_DIR = HERE / "logs"
#: Thales, the largest, takes about two minutes; this leaves a wide margin.
TIMEOUT_SECONDS = 20 * 60

_EXTRACTED_RE = re.compile(r"^Extracted (\d+) of (\d+) postings", re.M)
_CHANGES_RE = re.compile(r"^Since the previous run: (\d+) new, (\d+) gone", re.M)


def extractors(names: list[str]) -> list[tuple[str, list[str]]]:
    """Every extractor as (name, command): the per-site scripts, then boards.json."""
    found: dict[str, list[str]] = {
        path.name[len("extract") : -len("Positions.py")]: [str(path)]
        for path in sorted(HERE.glob("extract*Positions.py"))
    }
    if (HERE / "boards.json").exists():
        from extract_boards import load_boards

        for board in load_boards():
            if board["name"] in found:
                raise SystemExit(f"{board['name']} is both a script and a board in boards.json")
            found[board["name"]] = [str(HERE / "extract_boards.py"), board["name"]]
    if not names:
        return list(found.items())
    lower = {name.lower(): name for name in found}
    unknown = [name for name in names if name.lower() not in lower]
    if unknown:
        raise SystemExit(f"no extractor for {', '.join(unknown)}; have {', '.join(found)}")
    return [(lower[name.lower()], found[lower[name.lower()]]) for name in names]


def run(command: list[str]) -> tuple[int | None, str, float]:
    started = time.monotonic()
    try:
        result = subprocess.run(
            [sys.executable, *command],
            cwd=HERE,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
        return result.returncode, result.stdout + result.stderr, time.monotonic() - started
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return None, output + f"\nTIMED OUT after {TIMEOUT_SECONDS}s", time.monotonic() - started


def verdict(code: int | None, output: str) -> str:
    if code is None:
        return "FAILED"
    if code == 0:
        return "CHECK" if "\nCHECK:" in output else "OK"
    if code == 1 and "INCOMPLETE" in output:
        return "INCOMPLETE"
    if code == 2 and "NOTHING FOUND" in output:
        return "EMPTY"
    return "FAILED"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run every extractor and summarise.")
    parser.add_argument("sites", nargs="*", help="only these (default: all)")
    parser.add_argument(
        "--jobs", type=int, default=1, help="sites run at the same time (default 1, one after another)"
    )
    parser.add_argument("--list", action="store_true", help="list the extractors and stop")
    args = parser.parse_args()
    chosen = extractors(args.sites)
    if args.list:
        for name, _ in chosen:
            print(name)
        return 0

    LOG_DIR.mkdir(exist_ok=True)
    log_path = LOG_DIR / f"run_{datetime.now():%Y%m%d-%H%M%S}.log"
    rows = []
    lock = threading.Lock()

    def one(item: tuple[str, list[str]]) -> None:
        name, command = item
        if sys.stdout.isatty() and args.jobs == 1:
            print(f"{name:<14} …", end="", flush=True)
        code, output, seconds = run(command)
        status = verdict(code, output)
        extracted = _EXTRACTED_RE.search(output)
        changes = _CHANGES_RE.search(output)
        counts = f"{extracted.group(1)}/{extracted.group(2)}" if extracted else "-"
        delta = f"+{changes.group(1)} -{changes.group(2)}" if changes else ""
        # One site's output and summary line at a time, whatever finished first.
        with lock:
            log.write(f"\n{'=' * 70}\n{name}  (exit {code}, {seconds:.0f}s)\n{'=' * 70}\n{output}\n")
            log.flush()
            rows.append((name, status, counts, delta))
            print(f"\r{name:<14} {status:<11} {counts:>11}  {delta:<10} {seconds:5.0f}s", flush=True)

    with log_path.open("w", encoding="utf-8") as log:
        with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
            list(pool.map(one, chosen))

    bad = [name for name, status, _, _ in rows if status not in ("OK", "CHECK")]
    print(f"\n{len(rows) - len(bad)} of {len(rows)} sites OK. Full output: {log_path}")
    if bad:
        print(f"Look at: {', '.join(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

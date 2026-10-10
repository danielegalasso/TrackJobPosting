#!/usr/bin/env python3
"""Run the boards listed in boards.json.

Each board is one career site: a `name` (its folder, where latest.json and
runs/ go), the `company` shown in the portal, the `url` the user follows, a
`platform` (see platforms.py) and that platform's parameters. Output and the
completeness check are the same as the per-site scripts': extract_common.py.

Usage:
    python3 extract_boards.py NAME [NAME ...]    run these boards
    python3 extract_boards.py --list             list the boards
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from extract_common import finish_run

HERE = Path(__file__).resolve().parent
BOARDS_FILE = HERE / "boards.json"


def load_boards() -> list[dict[str, Any]]:
    boards = json.loads(BOARDS_FILE.read_text(encoding="utf-8"))
    names = [board["name"] for board in boards]
    duplicates = {name for name in names if names.count(name) > 1}
    if duplicates:
        raise SystemExit(f"boards.json: duplicate names {', '.join(sorted(duplicates))}")
    return [board for board in boards if not board.get("disabled")]


def run_board(board: dict[str, Any]) -> int:
    from platforms import PLATFORMS

    started = datetime.now()
    platform = PLATFORMS.get(board["platform"])
    if platform is None:
        print(f"{board['name']}: unknown platform {board['platform']!r}", file=sys.stderr)
        return 3
    query = board.get("query", "")
    print(f"{board['company']} [{board['platform']}] — {board['url']}")
    collected = platform(board)
    for note in collected.notes:
        print(f"  {note}")
    return finish_run(
        name=board["name"],
        out_dir=HERE / board["name"],
        query=query,
        source=board["url"],
        started=started,
        total=collected.total,
        records=collected.records,
        missing_detail=collected.missing_detail,
        company=board["company"],
        confirmed_empty=collected.confirmed_empty,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run boards listed in boards.json.")
    parser.add_argument("names", nargs="*")
    parser.add_argument("--list", action="store_true", help="list the boards and their platforms")
    args = parser.parse_args()
    boards = {board["name"].lower(): board for board in load_boards()}

    if args.list or not args.names:
        for board in boards.values():
            print(f"{board['name']:<28} {board['platform']:<16} {board['company']}")
        return 0

    status = 0
    for name in args.names:
        board = boards.get(name.lower())
        if board is None:
            print(f"no board named {name!r}", file=sys.stderr)
            status = max(status, 3)
            continue
        try:
            status = max(status, run_board(board))
        except Exception:  # noqa: BLE001 — report and go on to the next board
            traceback.print_exc()
            status = max(status, 4)
    return status


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Every Helsing posting, as shown on https://helsing.ai/jobs.

helsing.ai sits behind a Vercel Security Checkpoint that a script cannot
pass, and is not meant to. Its postings come from the Greenhouse board
`helsing`, whose public API serves the same list; the postings' own URLs
point back at helsing.ai/jobs/<id>. How the extraction works is described in
greenhouse_extract.py.  Usage:  python3 extractHelsingPositions.py
"""

import sys
from pathlib import Path

from greenhouse_extract import Board, main

HELSING = Board(
    name="Helsing",
    board="helsing",
    careers_url="https://helsing.ai/jobs",
    out_dir=Path(__file__).resolve().parent / "Helsing",
)

if __name__ == "__main__":
    sys.exit(main(HELSING))

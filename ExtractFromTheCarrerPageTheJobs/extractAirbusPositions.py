#!/usr/bin/env python3
"""Every Airbus posting matching a search on its Workday career site.

    https://ag.wd3.myworkdayjobs.com/en-US/Airbus?q=cyber%20security

How the extraction works, and the Workday traps it handles, is described in
workday_extract.py.  Usage:  python3 extractAirbusPositions.py [--query "..."]
"""

import sys
from pathlib import Path

from workday_extract import Board, main

AIRBUS = Board(
    name="Airbus",
    host="ag.wd3.myworkdayjobs.com",
    tenant="ag",
    site="Airbus",
    locale="en-US",
    default_query="cyber security",
    out_dir=Path(__file__).resolve().parent / "Airbus",
)

if __name__ == "__main__":
    sys.exit(main(AIRBUS))

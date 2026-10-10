#!/usr/bin/env python3
"""Every Leonardo posting matching a search on its Workday career site.

    https://leonardocompany.wd3.myworkdayjobs.com/it-IT/LeonardoCareerSite?q=cyber%20security

The site also carries Leonardo's subsidiaries (Telespazio, Leonardo DRS, …);
the `legal_entity` column says which one is hiring. How the extraction works
is described in workday_extract.py.
Usage:  python3 extractLeonardoPositions.py [--query "..."]
"""

import sys
from pathlib import Path

from workday_extract import Board, main

LEONARDO = Board(
    name="Leonardo",
    host="leonardocompany.wd3.myworkdayjobs.com",
    tenant="leonardocompany",
    site="LeonardoCareerSite",
    locale="it-IT",
    default_query="cyber security",
    out_dir=Path(__file__).resolve().parent / "Leonardo",
)

if __name__ == "__main__":
    sys.exit(main(LEONARDO))

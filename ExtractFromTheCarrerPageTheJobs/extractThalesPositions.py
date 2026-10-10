#!/usr/bin/env python3
"""Every Thales posting matching a search on its Phenom career site.

    https://careers.thalesgroup.com/global/en/search-results?keywords=cyber%20security

Thales applies through Workday (thales.wd3.myworkdayjobs.com/Careers), but
the count people see is the Phenom site's, so that is what is read. How the
extraction works, and the traps it handles, is described in phenom_extract.py.
Usage:  python3 extractThalesPositions.py [--query "..."]
"""

import sys
from pathlib import Path

from phenom_extract import Site, main

THALES = Site(
    name="Thales",
    host="careers.thalesgroup.com",
    country="global",
    language="en",
    locale="en_global",
    default_query="cyber security",
    out_dir=Path(__file__).resolve().parent / "Thales",
)

if __name__ == "__main__":
    sys.exit(main(THALES))

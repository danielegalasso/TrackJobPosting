#!/usr/bin/env python3
"""Every genua posting on its SAP SuccessFactors career site.

    https://jobs.genua.de/search?facetFilters=%7B%7D&pageNumber=0

genua is a security company, so the whole board is taken rather than a
search. How the extraction works is described in successfactors_extract.py.
Usage:  python3 extractGenuaPositions.py [--query "..."]
"""

import sys
from pathlib import Path

from successfactors_extract import Site, main

GENUA = Site(
    name="Genua",
    host="jobs.genua.de",
    locale="de_DE",
    default_query="",
    out_dir=Path(__file__).resolve().parent / "Genua",
    fields=(
        ("Einstiegslevel", "experience"),
        ("Bereich", "department"),
        ("Standort", "office"),
    ),
)

if __name__ == "__main__":
    sys.exit(main(GENUA))

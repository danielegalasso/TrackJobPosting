#!/usr/bin/env python3
"""Every secunet posting on its rexx job portal.

    https://jobs.secunet.com/stellenangebote.html

secunet is a security company, so the whole portal is taken rather than a
search. How the extraction works is described in rexx_extract.py.
Usage:  python3 extractSecunetPositions.py
"""

import sys
from pathlib import Path

from rexx_extract import Site, main

SECUNET = Site(
    name="Secunet",
    host="jobs.secunet.com",
    list_path="stellenangebote.html",
    unit="secunet",
    lang="de",
    out_dir=Path(__file__).resolve().parent / "Secunet",
    info_columns=("division", "contract", "location"),
)

if __name__ == "__main__":
    sys.exit(main(SECUNET))

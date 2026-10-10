#!/usr/bin/env python3
"""Every NATO civilian vacancy on its Taleo career section.

    https://nato.taleo.net/careersection/2/jobsearch.ftl?lang=en

The section covers the NATO International Staff and the agencies and
commands that recruit through it (NCIA, CMRE, NSPA, …); the `organisation`
column says which. How the extraction works, and the Taleo trap it gets
round, is described in taleo_extract.py.
Usage:  python3 extractNATOPositions.py
"""

import sys
from pathlib import Path

from taleo_extract import Section, main

NATO = Section(
    name="NATO",
    host="nato.taleo.net",
    section="2",
    lang="en",
    out_dir=Path(__file__).resolve().parent / "NATO",
)

if __name__ == "__main__":
    sys.exit(main(NATO))

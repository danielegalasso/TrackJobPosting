#!/usr/bin/env python3
"""Every NCC Group posting on its Pinpoint career site.

    https://nccgroup.pinpointhq.com/#js-careers-jobs-block

NCC Group is a security company, so the whole board is taken rather than a
search. How the extraction works is described in pinpoint_extract.py.
Usage:  python3 extractNCCGroupPositions.py
"""

import sys
from pathlib import Path

from pinpoint_extract import Site, main

NCC_GROUP = Site(
    name="NCCGroup",
    host="nccgroup.pinpointhq.com",
    out_dir=Path(__file__).resolve().parent / "NCCGroup",
    expected=75,
)

if __name__ == "__main__":
    sys.exit(main(NCC_GROUP))

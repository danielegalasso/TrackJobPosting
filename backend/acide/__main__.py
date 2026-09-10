"""`python -m acide` — run the portal with uvicorn."""

from __future__ import annotations

import argparse
import os


def main() -> None:
    parser = argparse.ArgumentParser(prog="acide", description="Run the ACIDE-Watch portal.")
    parser.add_argument("--host", default=os.environ.get("ACIDE_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("ACIDE_PORT", "8000")))
    parser.add_argument("--reload", action="store_true", help="auto-reload on code changes")
    args = parser.parse_args()

    import uvicorn

    uvicorn.run("acide.main:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()

"""`python -m acide` / `acide` — run the portal, or import a company list."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("acide.main:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def _import_companies(args: argparse.Namespace) -> int:
    """Resolve a curated organization list into indexable career feeds."""
    from . import config as config_module
    from . import paths
    from .watchlist import load_organizations, merge_targets, resolve_all

    source = Path(args.file).expanduser()
    if not source.exists():
        # A bare "[Errno 2]" leaves the reader guessing; the usual cause is a
        # relative path resolved against the wrong directory.
        print(
            f"no such file: {source}\n"
            f"  looked relative to {Path.cwd()}\n"
            "  pass the full path to your list, e.g. "
            "acide import-companies ~/Downloads/companies.json",
            file=sys.stderr,
        )
        return 1

    try:
        organizations = load_organizations(source)
    except (OSError, ValueError) as exc:
        print(f"could not read {source}: {exc}", file=sys.stderr)
        return 1

    if args.category:
        wanted = {item.strip().lower() for item in args.category.split(",")}
        organizations = [org for org in organizations if org.category.lower() in wanted]
    if args.limit:
        organizations = organizations[: args.limit]

    if not organizations:
        print("nothing to import — the filters matched no organizations", file=sys.stderr)
        return 1

    print(f"Resolving {len(organizations)} organization(s); one careers-page request each.")
    if args.guess:
        print("Guessing is on: unresolved names are also probed against the three ATS APIs.")
    print()

    report = resolve_all(
        organizations,
        guess=args.guess,
        workers=args.workers,
        on_log=print if args.verbose else None,
    )

    print()
    print(f"  resolved   {len(report.resolved):4} — these can be indexed now")
    print(f"  unresolved {len(report.unresolved):4}")
    for platform, count in report.by_other_ats().items():
        print(f"      {count:4}  {platform}")

    report_path = Path(args.report) if args.report else paths.DATA_DIR / "import-report.json"
    paths.ensure_dirs()
    report_path.write_text(report.to_json(), encoding="utf-8")
    print(f"\nFull report written to {report_path}")

    if not args.apply:
        print("\nNothing was saved. Re-run with --apply to add the resolved feeds to setup.json.")
        return 0

    config = config_module.load(refresh=True)
    before = len(config.targets)
    config.targets = merge_targets(config.targets, report.resolved)
    config_module.save(config)
    added = len(config.targets) - before
    print(f"\nsetup.json now has {len(config.targets)} targets ({added} added).")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="acide", description="Run the ACIDE-Watch portal.")
    subparsers = parser.add_subparsers(dest="command")

    serve = subparsers.add_parser("serve", help="run the portal (default)")
    for target in (parser, serve):
        target.add_argument("--host", default=os.environ.get("ACIDE_HOST", "127.0.0.1"))
        target.add_argument("--port", type=int, default=int(os.environ.get("ACIDE_PORT", "8000")))
        target.add_argument("--reload", action="store_true", help="auto-reload on code changes")
    serve.set_defaults(func=_serve)

    importer = subparsers.add_parser(
        "import-companies",
        help="resolve a curated organization list into career feeds",
        description=(
            "Reads a JSON array of organizations and works out which are on a "
            "supported ATS by fetching each careers page once."
        ),
    )
    importer.add_argument("file", help="path to the companies JSON file")
    importer.add_argument(
        "--apply", action="store_true", help="write the resolved feeds into setup.json"
    )
    importer.add_argument(
        "--guess",
        action="store_true",
        help="also probe likely board tokens when a careers page gives nothing away",
    )
    importer.add_argument("--category", help="only these categories, comma separated")
    importer.add_argument("--limit", type=int, help="stop after this many organizations")
    importer.add_argument("--workers", type=int, default=6, help="concurrent requests (default 6)")
    importer.add_argument("--report", help="where to write the JSON report")
    importer.add_argument("-v", "--verbose", action="store_true", help="log each organization")
    importer.set_defaults(func=_import_companies)

    args = parser.parse_args()
    handler = getattr(args, "func", _serve)
    raise SystemExit(handler(args))


if __name__ == "__main__":
    main()

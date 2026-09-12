"""`python -m acide` / `acide` — run the portal, or import a company list."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

#: How often the browser import writes its partial report. Ten organizations
#: is well under two minutes of work to lose.
CHECKPOINT_EVERY = 10


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("acide.main:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def _import_companies(args: argparse.Namespace) -> int:
    """Resolve a curated organization list into indexable career feeds."""
    from . import config as config_module
    from . import paths
    from .watchlist import (
        ImportReport,
        Organization,
        Resolution,
        load_organizations,
        merge_targets,
        organizations_from_report,
        resolve_all,
        resolve_all_with_browser,
    )

    def say(line: str) -> None:
        # Flushed. With stdout redirected to a file Python block-buffers it,
        # so an hour-long run shows nothing at all until it finishes — which
        # is indistinguishable from a hang.
        print(line, flush=True)

    def partial_report(
        done: list[Resolution], everything: list[Organization]
    ) -> ImportReport:
        """What has been resolved, plus what was never reached.

        The unreached are listed as unresolved so `--retry-report` picks up
        the whole remainder, not just the failures among the part that ran.
        """
        seen = {item.organization for item in done}
        pending = [
            Resolution(
                organization=org.organization,
                category=org.category,
                website=org.website,
                careers_page=org.careers_page,
                detail="not visited yet",
            )
            for org in everything
            if org.organization not in seen
        ]
        return ImportReport(resolutions=[*done, *pending])

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
        if args.retry_report:
            organizations = organizations_from_report(source)
        else:
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

    paths.ensure_dirs()
    report_path = Path(args.report) if args.report else paths.DATA_DIR / "import-report.json"

    if args.browser:
        where = f"your Chrome at {args.cdp_url}" if args.cdp_url else "a bundled Chromium"
        say(f"Resolving {len(organizations)} organization(s) in {where}.")
        say("Pages are visited one at a time; this is slower than the HTTP pass by design.")
        say(f"Progress is saved to {report_path} as it goes; resume with --retry-report.")
        say("")
        from .browser_discovery import BrowserUnavailable

        done: list[Resolution] = []

        def checkpoint(resolution: Resolution) -> None:
            done.append(resolution)
            if len(done) % CHECKPOINT_EVERY == 0:
                report_path.write_text(
                    partial_report(done, organizations).to_json(), encoding="utf-8"
                )

        try:
            report = resolve_all_with_browser(
                organizations,
                cdp_url=args.cdp_url,
                headless=not args.show_browser,
                settle_ms=args.settle_ms,
                delay_seconds=args.delay,
                executable_path=args.chrome_path,
                user_data_dir=args.profile_dir,
                on_log=say if args.verbose else None,
                on_resolution=checkpoint,
            )
        except BrowserUnavailable as exc:
            print(str(exc), file=sys.stderr)
            return 1
        except (KeyboardInterrupt, Exception) as exc:
            report_path.write_text(
                partial_report(done, organizations).to_json(), encoding="utf-8"
            )
            remaining = len(organizations) - len(done)
            print(
                f"\nStopped after {len(done)} of {len(organizations)}; "
                f"{remaining} not visited.\n"
                f"Progress saved to {report_path}.\n"
                f"Resume with:\n"
                f"  acide import-companies {report_path} --retry-report --browser -v",
                file=sys.stderr,
            )
            if isinstance(exc, KeyboardInterrupt):
                # Ctrl-C is a decision, not a crash; a traceback here reads
                # as one and buries the resume instructions above it.
                return 130
            raise
    else:
        say(f"Resolving {len(organizations)} organization(s); one careers-page request each.")
        if args.guess:
            say("Guessing is on: unresolved names are also probed against the three ATS APIs.")
        say("")

        report = resolve_all(
            organizations,
            guess=args.guess,
            workers=args.workers,
            on_log=say if args.verbose else None,
        )

    print()
    print(f"  resolved   {len(report.resolved):4} — these can be indexed now")
    print(f"  unresolved {len(report.unresolved):4}")
    for platform, count in report.by_other_ats().items():
        print(f"      {count:4}  {platform}")

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


def _raw_entries_by_name(source: Path) -> dict[str, dict]:
    """The companies file as written, keyed by organization name.

    Rewriting a curated list should give it back with only the one field
    this tool is entitled to change.
    """
    import json

    try:
        raw = json.loads(source.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, list):
        return {}
    entries: dict[str, dict] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("organization") or entry.get("company") or "").strip()
        if name:
            entries[name] = entry
    return entries


def _check_urls(args: argparse.Namespace) -> int:
    """Check every careers URL and propose a replacement for the dead ones."""
    import json

    from . import paths
    from .linkcheck import repair_all
    from .watchlist import load_organizations, organizations_from_report

    source = Path(args.file).expanduser()
    if not source.exists():
        print(f"no such file: {source}\n  looked relative to {Path.cwd()}", file=sys.stderr)
        return 1

    try:
        if args.retry_report:
            organizations = organizations_from_report(source)
        else:
            organizations = load_organizations(source)
    except (OSError, ValueError) as exc:
        print(f"could not read {source}: {exc}", file=sys.stderr)
        return 1

    if args.category:
        wanted = {item.strip().lower() for item in args.category.split(",")}
        organizations = [org for org in organizations if org.category.lower() in wanted]
    if args.limit:
        organizations = organizations[: args.limit]

    def say(line: str) -> None:
        print(line, flush=True)

    say(f"Checking {len(organizations)} careers page(s).")
    say("Dead links are repaired from the site's own navigation where possible.")
    say("")

    repairs = repair_all(
        organizations, workers=args.workers, on_log=say if args.verbose else None
    )

    counts: dict[str, int] = {}
    for repair in repairs:
        counts[repair.verdict] = counts.get(repair.verdict, 0) + 1
    say("")
    for verdict in ("working", "moved", "repaired", "broken"):
        if counts.get(verdict):
            say(f"  {counts[verdict]:4}  {verdict}")

    # A redirect the site offered but which landed nowhere careers-like is
    # the weakest answer here, and worth separating from the rest.
    weak = sum(1 for r in repairs if "no careers link found there" in r.how)
    if weak:
        say(f"\n  {weak} of those redirect to a page with no careers signal —")
        say("  usually a retired path pointed at the homepage. Worth a look.")

    paths.ensure_dirs()
    report_path = Path(args.report) if args.report else paths.DATA_DIR / "url-check.json"
    report_path.write_text(
        json.dumps(
            [
                {
                    "organization": r.organization,
                    "category": r.category,
                    "website": r.website,
                    "original": r.original,
                    "verdict": r.verdict,
                    "status": r.status,
                    "suggested": r.suggested,
                    "how": r.how,
                    "note": r.note,
                }
                for r in repairs
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nReport written to {report_path}")

    if args.write:
        fixed = Path(args.write).expanduser()
        by_name = {r.organization: r for r in repairs}
        # Start from the entries as they were written, so fields this tool
        # knows nothing about — an id, a note, anything the operator keeps
        # alongside — survive the round trip. Only careers_page is rewritten.
        originals = _raw_entries_by_name(source) if not args.retry_report else {}
        entries = []
        for org in organizations:
            repair = by_name.get(org.organization)
            entry = dict(originals.get(org.organization) or {})
            entry.setdefault("organization", org.organization)
            entry.setdefault("category", org.category)
            entry.setdefault("website", org.website)
            # Only a URL actually seen to work replaces the original.
            entry["careers_page"] = (
                repair.suggested if repair and repair.suggested else org.careers_page
            )
            entries.append(entry)
        fixed.write_text(json.dumps(entries, indent=2), encoding="utf-8")
        print(f"Corrected list written to {fixed}")
    else:
        print("\nNothing was rewritten. Use --write FILE to save a corrected list.")
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
    importer.add_argument(
        "--retry-report",
        action="store_true",
        help="treat FILE as a previous import report and retry only its unresolved entries",
    )
    browser_group = importer.add_argument_group(
        "browser mode",
        "For careers pages that only render their board once JavaScript has run — "
        "roughly half of a typical list. Slower, and visits one page at a time.",
    )
    browser_group.add_argument(
        "--browser", action="store_true", help="resolve in a real browser instead of plain HTTP"
    )
    browser_group.add_argument(
        "--cdp-url",
        help=(
            "attach to a Chrome you started yourself, e.g. http://localhost:9222 "
            "(start it with --remote-debugging-port=9222)"
        ),
    )
    browser_group.add_argument(
        "--chrome-path",
        help="drive a browser binary you already have, e.g. /usr/bin/google-chrome",
    )
    browser_group.add_argument(
        "--profile-dir",
        help=(
            "keep a browser profile between runs, e.g. ~/.acide-chrome — cookie "
            "consent and sessions persist, so sites stop showing an interstitial "
            "over the board on every visit"
        ),
    )
    browser_group.add_argument(
        "--show-browser", action="store_true", help="run headed, so you can watch it"
    )
    browser_group.add_argument(
        "--settle-ms", type=int, default=2500, help="wait per page for scripts to finish"
    )
    browser_group.add_argument(
        "--delay", type=float, default=2.0, help="seconds between pages (default 2)"
    )
    importer.set_defaults(func=_import_companies)

    checker = subparsers.add_parser(
        "check-urls",
        help="check careers URLs and repair the dead ones",
        description=(
            "Fetches every careers page. Dead links are repaired by following "
            "redirects, reading the site's own navigation, and finally by "
            "trying conventional paths — each candidate verified before it is "
            "proposed."
        ),
    )
    checker.add_argument("file", help="companies JSON, or an import report with --retry-report")
    checker.add_argument("--write", help="write a corrected companies list to this path")
    checker.add_argument("--report", help="where to write the JSON check report")
    checker.add_argument("--category", help="only these categories, comma separated")
    checker.add_argument("--limit", type=int, help="stop after this many organizations")
    checker.add_argument("--workers", type=int, default=6, help="concurrent requests (default 6)")
    checker.add_argument(
        "--retry-report",
        action="store_true",
        help="treat FILE as an import report and check only its unresolved entries",
    )
    checker.add_argument("-v", "--verbose", action="store_true", help="log each organization")
    checker.set_defaults(func=_check_urls)

    args = parser.parse_args()
    handler = getattr(args, "func", _serve)
    raise SystemExit(handler(args))


if __name__ == "__main__":
    main()

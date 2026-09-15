"""`python -m acide` / `acide` — run the portal, or import a company list."""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

#: How often the browser import writes its partial report. Ten organizations
#: is well under two minutes of work to lose.
CHECKPOINT_EVERY = 10


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("acide.main:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def _shared_pages(resolutions: list) -> dict[str, list[str]]:
    """Careers URLs claimed by more than one organization.

    A curated list repeats itself: corporate groups run one careers site for
    every division, which is fine, while unrelated bodies sharing a portal
    usually means the specific page was never found.
    """
    by_url: dict[str, list[str]] = {}
    for item in resolutions:
        if item.careers_page:
            by_url.setdefault(item.careers_page, []).append(item.organization)
    return {url: names for url, names in by_url.items() if len(names) > 1}


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

    resolved = report.resolved
    # Several organizations can share one board — eight Thales divisions do —
    # and counting postings per organization multiplies them. On a real run
    # that read 54,755 where the boards actually hold 23,129.
    boards = {
        (item.source_type, item.board_token): (item.job_count or 0) for item in resolved
    }
    print()
    print(f"  resolved   {len(resolved):4} — these can be indexed now")
    if boards:
        print(f"             {len(boards):4} distinct boards, "
              f"{sum(boards.values()):,} postings between them")
    print(f"  unresolved {len(report.unresolved):4}")
    for platform, count in report.by_other_ats().items():
        print(f"      {count:4}  {platform}")

    shared = _shared_pages(report.resolutions)
    if shared:
        redundant = sum(len(names) - 1 for names in shared.values())
        print(
            f"\n  {len(shared)} careers page(s) are listed by more than one organization "
            f"({redundant} fewer visits)."
        )
        print("  Each was visited once. Where a group really does run one careers")
        print("  site this is correct; where it does not, give them separate URLs:")
        for url, names in sorted(shared.items(), key=lambda kv: -len(kv[1]))[:5]:
            print(f"      {len(names)}x {url}")
            print(f"          {', '.join(names[:6])}{' …' if len(names) > 6 else ''}")

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


#: The source types whose board token *is* a URL, and which therefore rot the
#: way a curated careers link does.
URL_SOURCE_TYPES = ("browser", "jsonld")


def _check_target_urls(args: argparse.Namespace) -> int:
    """Repair the careers URLs of the configured targets.

    Resolution turns a careers page into a target once, and the page can move
    afterwards: an overnight pass over 469 sources reported
    `TU Munchen: https://www.tum.de/en/about-tum/working-at-tum: HTTP 404`,
    and nothing short of redoing the whole resolution pipeline would have
    fixed it — `check-urls` reads a companies file, and by then the companies
    file is not what the crawl reads. This applies the same repair ladder to
    what `setup.json` actually holds.

    Only `browser` and `jsonld` targets are checked: every other source type
    carries a board token, and a board that stops answering is a different
    problem with a different answer.
    """
    import json

    from . import config as config_module
    from . import db, paths
    from .linkcheck import repair_all
    from .watchlist import Organization

    config = config_module.load(refresh=True)
    targets = [
        target
        for target in config.targets
        if target.source_type in URL_SOURCE_TYPES
        and target.board_token.strip().lower().startswith(("http://", "https://"))
    ]
    if args.failed:
        failed = db.failed_source_keys()
        before = len(targets)
        targets = [
            target
            for target in targets
            if (target.source_type, target.board_token.lower()) in failed
        ]
        print(f"Only the {len(targets)} of {before} that failed their last crawl.")
    if args.limit:
        targets = targets[: args.limit]
    if not targets:
        print(
            "no rendered or JSON-LD target has a URL to check"
            + (" that also failed its last crawl" if args.failed else "")
            + " — nothing to do."
        )
        return 0

    # Several targets can share one careers page — 631 organizations resolved
    # to 581 distinct URLs — so each URL is checked once and the answer is
    # applied to every target that carries it.
    by_url: dict[str, list] = {}
    for target in targets:
        by_url.setdefault(target.board_token.strip(), []).append(target)

    def say(line: str) -> None:
        print(line, flush=True)

    say(f"Checking {len(by_url)} URL(s) across {len(targets)} configured target(s).")

    organizations = [
        Organization(organization=members[0].company, careers_page=url)
        for url, members in by_url.items()
    ]
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

    # A page a browser renders can still refuse a plain request — and often
    # with 404 rather than 403, which reads as "deleted" and is not. 126 pages
    # of one probe report were refusals of exactly this kind. So a source the
    # last crawl *read* is never repointed on the strength of a plain fetch,
    # however dead that fetch says it is.
    crawled_ok = {
        (row["source_type"], row["board_token"].lower())
        for row in db.source_states()
        if row["status"] == "ok"
    }

    def was_read(repair) -> bool:
        return any(
            (target.source_type, target.board_token.strip().lower()) in crawled_ok
            for target in by_url[repair.original]
        )

    fixable, dead, refusals = [], [], []
    for repair in repairs:
        if not repair.needs_attention:
            continue
        if was_read(repair):
            refusals.append(repair)
        elif repair.suggested:
            fixable.append(repair)
        else:
            dead.append(repair)

    if refusals:
        say("\nrefuses a plain request, but the crawl read it — left alone:")
        for repair in refusals:
            for target in by_url[repair.original]:
                say(f"  {target.company}  [{target.source_type}]")
            say(f"      {repair.original}  ({repair.note})")

    if fixable:
        say("\nwould be repointed:")
        for repair in fixable:
            for target in by_url[repair.original]:
                say(f"  {target.company}  [{target.source_type}]")
            say(f"      {repair.original}")
            say(f"   →  {repair.suggested}   ({repair.how})")
    if dead:
        say("\nno working replacement found — these need a human:")
        for repair in dead:
            for target in by_url[repair.original]:
                say(f"  {target.company}  [{target.source_type}]")
            say(f"      {repair.original}  {repair.note}")

    paths.ensure_dirs()
    report_path = Path(args.report) if args.report else paths.DATA_DIR / "target-url-check.json"
    report_path.write_text(
        json.dumps(
            [
                {
                    "companies": [target.company for target in by_url[repair.original]],
                    "source_type": by_url[repair.original][0].source_type,
                    "original": repair.original,
                    "verdict": (
                        "refuses plain HTTP"
                        if repair in refusals
                        else repair.verdict
                    ),
                    "status": repair.status,
                    "suggested": repair.suggested,
                    "how": repair.how,
                    "note": repair.note,
                }
                for repair in repairs
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nReport written to {report_path}")

    if not fixable:
        print(
            "\nNothing to rewrite — "
            + (
                "no working replacement was found for the dead one(s)."
                if dead
                else "every URL still answers."
            )
        )
        return 0
    if not args.apply:
        print(
            f"\nNothing was saved. Re-run with --apply to point {len(fixable)} target(s) "
            "at the URL that answers."
        )
        return 0

    # Only a URL that was fetched and seen to work replaces the one in place.
    repointed = {repair.original: repair.suggested for repair in fixable}
    changed = 0
    for target in config.targets:
        suggested = repointed.get(target.board_token.strip())
        if suggested:
            target.board_token = suggested
            changed += 1
    config_module.save(config)
    print(f"\nsetup.json updated: {changed} target(s) repointed.")
    print("Crawl just those again with:  acide inspect --retry-failed")
    return 0


def _check_urls(args: argparse.Namespace) -> int:
    """Check every careers URL and propose a replacement for the dead ones."""
    if args.targets:
        return _check_target_urls(args)

    import json

    from . import paths
    from .linkcheck import repair_all
    from .watchlist import load_organizations, organizations_from_report

    if not args.file:
        print(
            "give a companies file, or --targets to check the URLs already "
            "configured in setup.json",
            file=sys.stderr,
        )
        return 1

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


def _probe(args: argparse.Namespace) -> int:
    """Say what the unresolved careers pages actually contain, without a browser."""
    from . import paths
    from .probe import probe_all
    from .watchlist import load_organizations, organizations_from_report

    source = Path(args.file).expanduser()
    if not source.exists():
        print(f"no such file: {source}\n  looked relative to {Path.cwd()}", file=sys.stderr)
        return 1

    try:
        organizations = (
            load_organizations(source) if args.all else organizations_from_report(source)
        )
    except (OSError, ValueError) as exc:
        print(f"could not read {source}: {exc}", file=sys.stderr)
        return 1

    if args.limit:
        organizations = organizations[: args.limit]
    if not organizations:
        print("nothing to probe", file=sys.stderr)
        return 1

    def say(line: str) -> None:
        print(line, flush=True)

    say(f"Probing {len(organizations)} careers page(s) over plain HTTP — no browser.")
    say("This is a dry run: nothing is saved to setup.json and no board is contacted.")
    say("")

    report = probe_all(
        organizations, workers=args.workers, on_log=say if args.verbose else None
    )

    say("")
    say(f"  {report.summary_line()}")
    say("")
    for verdict, count in report.by_verdict().items():
        say(f"  {count:4}  {verdict}")
    by_type = report.by_source_type()
    if by_type:
        say("")
        say("  of those that would resolve now:")
        for source_type, count in by_type.items():
            say(f"      {count:4}  {source_type}")

    paths.ensure_dirs()
    report_path = Path(args.report) if args.report else paths.DATA_DIR / "probe-report.json"
    report_path.write_text(report.to_json(), encoding="utf-8")
    say(f"\nReport written to {report_path}")
    return 0


def _adopt_browser(args: argparse.Namespace) -> int:
    """Turn the pages a probe could not read into rendered targets."""
    import json

    from . import config as config_module
    from .probe import browser_targets, report_from_json
    from .watchlist import add_targets

    source = Path(args.file).expanduser()
    if not source.exists():
        print(f"no such file: {source}\n  looked relative to {Path.cwd()}", file=sys.stderr)
        return 1
    try:
        payload = json.loads(source.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        print(f"could not read {source}: {exc}", file=sys.stderr)
        return 1
    if not isinstance(payload, dict) or "probes" not in payload:
        print(
            f"{source} is not a probe report. Run `acide probe` first:\n"
            "  acide probe data/import-report.json",
            file=sys.stderr,
        )
        return 1

    candidates = list(
        browser_targets(report_from_json(payload), include_refusals=not args.skip_refusals)
    )
    if args.category:
        print("--category is not available here; a probe report carries no categories.",
              file=sys.stderr)
    if args.limit:
        candidates = candidates[: args.limit]

    if not candidates:
        print("nothing to adopt — no page in that report needs rendering.")
        return 0

    print(f"{len(candidates)} careers page(s) would be indexed by rendering them:")
    for target in candidates:
        print(f"  {target.company}")
        print(f"      {target.board_token}")

    if not args.apply:
        print(
            "\nNothing was saved. Re-run with --apply to add these to setup.json."
            "\nRendering is slow — try a handful first with --limit 5."
        )
        return 0

    config = config_module.load(refresh=True)
    before = len(config.targets)
    config.targets = add_targets(config.targets, candidates)
    config_module.save(config)
    added = len(config.targets) - before
    print(f"\nsetup.json now has {len(config.targets)} targets ({added} added).")
    if added:
        print("Install the browser if you have not: playwright install chromium")
    return 0


def _inspect(args: argparse.Namespace) -> int:
    """Run one inspection pass and exit — for an unattended batch.

    The portal's Run button posts to the API, which needs the server up and a
    browser tab open. A run over several hundred sources takes hours and wants
    neither: this is the same pass, driven from a terminal, logging to stdout
    so it can be redirected to a file and left overnight.
    """
    import logging

    from . import config as config_module
    from .spider import runner

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
        force=True,
    )
    # One INFO line per HTTP request is most of the file on a run of several
    # thousand postings, and none of it is about the run.
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # Unbuffered, so a redirected log is readable while the run is going.
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(line_buffering=True)

    config = config_module.load(refresh=True)
    targets = [target for target in config.targets if target.enabled]
    if args.source_type:
        wanted = {name.strip() for name in args.source_type.split(",") if name.strip()}
        targets = [target for target in targets if target.source_type in wanted]

    if args.retry_failed:
        from . import db

        db.init_db()
        # Never attempted counts as unfinished, so this both retries the
        # failures and resumes a pass that was stopped half way — the same
        # operation, since what matters is which sources already succeeded.
        done = db.succeeded_source_keys()
        before = len(targets)
        targets = [
            target for target in targets
            if (target.source_type, target.board_token.lower()) not in done
        ]
        print(f"Skipping {before - len(targets)} source(s) that already succeeded.")

    if args.limit:
        targets = targets[: args.limit]
    if not targets:
        print("no enabled targets match — nothing to inspect", file=sys.stderr)
        return 1
    config.targets = targets

    kinds: dict[str, int] = {}
    for target in targets:
        kinds[target.source_type] = kinds.get(target.source_type, 0) + 1
    print(f"Inspecting {len(targets)} source(s): "
          + ", ".join(f"{count} {name}" for name, count in sorted(kinds.items())))
    if not config.spider.search_terms and any(
        target.source_type in ("workday", "smartrecruiters") for target in targets
    ):
        print(
            "  note: spider.search_terms is empty, so a corporate board returns "
            "max_jobs_per_source of whatever it lists rather than a keyword slice. "
            "That is a bigger backlog to score, not a bigger bill to crawl."
        )
    if kinds.get("browser"):
        print(f"  {kinds['browser']} source(s) are rendered pages — expect hours, not minutes.")
    print()

    if config.openrouter.api_key and not args.skip_preflight and not args.no_score:
        from .llm import InferenceError, OpenRouterClient

        print("Checking the evaluator before crawling …", flush=True)
        try:
            with OpenRouterClient(config) as client:
                client.preflight()
        except InferenceError as exc:
            print(f"\n  evaluator check FAILED: {exc}\n", file=sys.stderr)
            print(
                "Nothing was crawled. Every posting would have failed the same way, "
                "so the run stops here rather than spending hours to find out.\n"
                "Fix the model or key in Settings, or pass --skip-preflight to crawl "
                "without scoring.",
                file=sys.stderr,
            )
            return 1
        print("  evaluator ok.")
        print()

    if args.no_score:
        print("Crawling only — postings are stored unscored, for `acide score` later.")
        print()

    try:
        summary = runner.run_once(
            config, send_alerts=not args.no_alerts, score=not args.no_score
        )
    except KeyboardInterrupt:
        print(
            "\nStopped. Every source that finished is saved; re-running skips it.",
            file=sys.stderr,
        )
        return 130

    print()
    print(f"  sources polled   {summary.sources_polled}")
    print(f"  postings seen    {summary.postings_seen}")
    print(f"  postings new     {summary.postings_new}")
    print(f"  postings scored  {summary.postings_scored}")
    print(f"  alert emails     {summary.alerts_sent}")
    if args.no_score:
        from . import db

        waiting = db.count_unscored()
        print(f"\n  {waiting} posting(s) waiting to be scored:")
        print("      acide score            # what is waiting, and what it would cost")
        print("      acide score --yes      # judge them")
    if summary.errors:
        from . import failures

        # Grouped, not listed: one bad response schema can account for
        # thousands of these, and fifteen copies of it hide the single rotted
        # URL that is the other thing worth knowing.
        causes = failures.group(summary.errors)
        print(f"\n  {len(summary.errors)} error(s), {len(causes)} distinct cause(s):")
        for line in failures.render(causes):
            print(f"    {line}")
    return 0


def _score_pending(args: argparse.Namespace) -> int:
    """Judge postings that were found but never scored.

    Crawling and judging are separate: a posting is stored the moment it is
    found, so a scoring failure costs nothing already crawled, and the bill
    can be paid in batches rather than all at once.
    """
    import logging

    from . import config as config_module
    from . import db, resume
    from .llm import InferenceError, OpenRouterClient
    from .spider.runner import _score

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S",
        stream=sys.stdout, force=True,
    )
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    config = config_module.load(refresh=True)
    if not config.openrouter.api_key:
        print("no OpenRouter key configured — nothing can be scored", file=sys.stderr)
        return 1

    waiting = db.count_unscored()
    if not waiting:
        print("nothing is waiting to be scored.")
        return 0

    batch = db.unscored_postings(limit=args.limit or waiting)
    print(f"{waiting} posting(s) waiting; scoring {len(batch)}.")
    if not args.yes:
        print("This makes one model call per posting. Re-run with --yes to go ahead.")
        return 0

    try:
        with OpenRouterClient(config) as client:
            print("Checking the evaluator first …", flush=True)
            try:
                client.preflight()
            except InferenceError as exc:
                print(f"\n  evaluator check FAILED: {exc}", file=sys.stderr)
                print("Nothing was scored.", file=sys.stderr)
                return 1
            print("  evaluator ok.\n")

            from .models import SpiderRunSummary

            summary = SpiderRunSummary(started_at=datetime.now(UTC))
            _score(batch, config, resume.active_text(), summary, client)
    except KeyboardInterrupt:
        print("\nStopped. Everything scored so far is saved.", file=sys.stderr)
        return 130

    print()
    print(f"  scored     {summary.postings_scored}")
    print(f"  failed     {len(summary.errors)}")
    print(f"  remaining  {db.count_unscored()}")
    for error in summary.errors[:5]:
        print(f"      {error}")
    return 0


def _sources(args: argparse.Namespace) -> int:
    """Report how each source went the last time it was crawled."""
    from . import config as config_module
    from . import db

    db.init_db()
    states = db.source_states()
    if not states:
        print("no source has been crawled yet — run `acide inspect` first.")
        return 0

    configured = {
        (target.source_type, target.board_token.lower()): target
        for target in config_module.load(refresh=True).targets
    }
    seen = {(row["source_type"], row["board_token"].lower()) for row in states}
    never = [target for key, target in configured.items() if key not in seen]

    ok = [row for row in states if row["status"] == "ok"]
    failed = [row for row in states if row["status"] != "ok"]
    empty = [row for row in ok if not row["postings"]]

    print(f"  {len(ok):4}  succeeded ({len(empty)} of them found nothing)")
    print(f"  {len(failed):4}  failed")
    print(f"  {len(never):4}  never attempted")
    print(f"  {sum(row['postings'] for row in ok):4}  postings found in total")

    if failed and not args.quiet:
        from . import failures

        # By cause rather than by company: a connector that broke takes every
        # board it serves down with it, and that reads as one line of work
        # rather than thirty entries to scroll past.
        causes = failures.group(
            f"{row['company']} [{row['source_type']}]: {row['detail']}" for row in failed
        )
        print(f"\nfailed, by cause ({len(causes)} distinct):")
        for line in failures.render(causes, limit=args.limit or 12, subjects=8):
            print(line)
    if never and not args.quiet:
        print("\nnever attempted:")
        for target in never[: args.limit or len(never)]:
            print(f"  {target.company}  [{target.source_type}]")

    if failed or never:
        print("\nRe-run only these with:")
        print("  acide inspect --retry-failed")
    if any(row["source_type"] in URL_SOURCE_TYPES for row in failed):
        # A rendered page fails because its URL moved far more often than
        # because the page changed, and that is repairable without redoing
        # resolution.
        print("\nA rendered page that will not load may simply have moved:")
        print("  acide check-urls --targets --failed        # propose the URL that answers")
        print("  acide check-urls --targets --failed --apply")
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
            "proposed. With --targets it checks the URLs already configured in "
            "setup.json instead of a file, which is what a crawl actually "
            "reads once resolution is done."
        ),
    )
    checker.add_argument(
        "file",
        nargs="?",
        help="companies JSON, or an import report with --retry-report",
    )
    checker.add_argument(
        "--targets",
        action="store_true",
        help="check the URLs of the configured browser/jsonld targets instead of a file",
    )
    checker.add_argument(
        "--failed",
        action="store_true",
        help="with --targets, only those whose last crawl failed",
    )
    checker.add_argument(
        "--apply",
        action="store_true",
        help="with --targets, save the repaired URLs to setup.json",
    )
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

    prober = subparsers.add_parser(
        "probe",
        help="say what unresolved careers pages actually contain (no browser)",
        description=(
            "Fetches each unresolved careers page over plain HTTP and reports "
            "what discovery would now make of it: a board it can read, the "
            "page's own schema.org postings, a platform with no connector, or "
            "a page that genuinely needs a browser. Minutes rather than the "
            "hour a browser pass costs, and nothing is saved."
        ),
    )
    prober.add_argument("file", help="an import report, or a companies list with --all")
    prober.add_argument(
        "--all",
        action="store_true",
        help="treat FILE as a companies list and probe every entry, not only the unresolved",
    )
    prober.add_argument("--limit", type=int, help="stop after this many")
    prober.add_argument("--workers", type=int, default=8, help="concurrent requests (default 8)")
    prober.add_argument("--report", help="where to write the JSON report")
    prober.add_argument("-v", "--verbose", action="store_true", help="log each page")
    prober.set_defaults(func=_probe)

    adopter = subparsers.add_parser(
        "adopt-browser",
        help="add rendered targets for the pages a probe could not read",
        description=(
            "Reads a probe report and configures the pages that need rendering "
            "as `browser` targets. These are indexed by opening the careers "
            "page in a browser and reading its job list, which is slower than "
            "any API — try a handful with --limit first."
        ),
    )
    adopter.add_argument("file", help="a probe report from `acide probe`")
    adopter.add_argument(
        "--apply", action="store_true", help="write the targets into setup.json"
    )
    adopter.add_argument("--limit", type=int, help="adopt only this many")
    adopter.add_argument(
        "--skip-refusals",
        action="store_true",
        help="only pages that need JavaScript, not those that refused a plain request",
    )
    adopter.add_argument("--category", help=argparse.SUPPRESS)
    adopter.set_defaults(func=_adopt_browser)

    inspector = subparsers.add_parser(
        "inspect",
        help="run one inspection pass and exit (for an unattended batch)",
        description=(
            "Fetches every enabled source, scores the new postings and exits. "
            "The same pass the portal's Run button triggers, but without "
            "needing the server or a browser tab — logs go to stdout, so it can "
            "be redirected to a file and left to run. Each source is saved as it "
            "finishes, so stopping costs at most the source in flight."
        ),
    )
    inspector.add_argument(
        "--no-alerts", action="store_true", help="do not send digest email at the end"
    )
    inspector.add_argument(
        "--source-type",
        help="only these source types, comma separated, e.g. browser or greenhouse,ashby",
    )
    inspector.add_argument("--limit", type=int, help="stop after this many sources")
    inspector.add_argument(
        "--retry-failed",
        action="store_true",
        help=(
            "only sources that failed last time or were never reached — also "
            "how an interrupted pass is resumed"
        ),
    )
    inspector.add_argument(
        "--no-score",
        action="store_true",
        help="find and store postings without judging any — score them later with `acide score`",
    )
    inspector.add_argument(
        "--skip-preflight",
        action="store_true",
        help="do not test the evaluator first (it costs one cheap call)",
    )
    inspector.set_defaults(func=_inspect)

    scorer = subparsers.add_parser(
        "score",
        help="judge postings that were found but never scored",
        description=(
            "A posting is stored the moment it is found, and judged separately. "
            "This scores what is waiting — in batches, since every posting is "
            "one model call. A dry run reports the backlog; --yes spends it."
        ),
    )
    scorer.add_argument("--limit", type=int, help="score at most this many")
    scorer.add_argument("--yes", action="store_true", help="actually spend the calls")
    scorer.set_defaults(func=_score_pending)

    reporter = subparsers.add_parser(
        "sources",
        help="how each source went the last time it was crawled",
        description=(
            "A pass over several hundred rendered pages costs hours, so each "
            "source's outcome is remembered. This reports them, and names the "
            "ones worth repeating."
        ),
    )
    reporter.add_argument("--limit", type=int, help="show at most this many of each")
    reporter.add_argument("-q", "--quiet", action="store_true", help="counts only")
    reporter.set_defaults(func=_sources)

    args = parser.parse_args()
    handler = getattr(args, "func", _serve)
    raise SystemExit(handler(args))


if __name__ == "__main__":
    main()

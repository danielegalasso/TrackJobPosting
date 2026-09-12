"""Importing a curated organization list into indexable career feeds."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from acide.discovery import candidate_tokens, discover_in_html
from acide.models import TargetSource
from acide.watchlist import (
    Organization,
    load_organizations,
    merge_targets,
    resolve_all,
)

GREENHOUSE = "https://boards-api.greenhouse.io/v1/boards"
LEVER = "https://api.lever.co/v0/postings"
ASHBY = "https://api.ashbyhq.com/posting-api/job-board"


# ---------------------------------------------------------------------------
# Reading the ATS out of a careers page
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("html", "source_type", "token"),
    [
        (
            '<iframe src="https://boards.greenhouse.io/embed/job_board?for=andurilindustries">',
            "greenhouse",
            "andurilindustries",
        ),
        ('<a href="https://job-boards.greenhouse.io/nviso">Open roles</a>', "greenhouse", "nviso"),
        ('<a href="https://jobs.lever.co/helsing">Careers</a>', "lever", "helsing"),
        ('<a href="https://jobs.eu.lever.co/quantum">Careers</a>', "lever", "quantum"),
        ('<a href="https://jobs.ashbyhq.com/Wiz">Join us</a>', "ashby", "Wiz"),
        (
            '<script>fetch("https://api.ashbyhq.com/posting-api/job-board/vyoma")</script>',
            "ashby",
            "vyoma",
        ),
    ],
)
def test_a_careers_page_reveals_its_board(html, source_type, token):
    """A careers URL is not a board endpoint, but the page names one."""
    found = discover_in_html(html)
    assert found.supported
    assert (found.source_type, found.board_token) == (source_type, token)


def test_an_embed_wins_over_a_bare_link():
    html = """
      <a href="https://boards.greenhouse.io/careers">All jobs</a>
      <iframe src="https://boards.greenhouse.io/embed/job_board?for=realtoken"></iframe>
    """
    assert discover_in_html(html).board_token == "realtoken"


def test_structural_path_segments_are_not_mistaken_for_tokens():
    found = discover_in_html('<img src="https://boards.greenhouse.io/static/x.png">')
    assert found.supported is False


@pytest.mark.parametrize(
    ("html", "platform"),
    [
        ('<a href="https://leonardo.wd3.myworkdayjobs.com/careers">', "workday"),
        ('<a href="https://career5.successfactors.eu/career?company=x">', "successfactors"),
        ('<a href="https://eu-careers.europa.eu/en/job-opportunities">', "eu-careers"),
        ('<a href="https://jobs.smartrecruiters.com/Acme">', "smartrecruiters"),
        ('<a href="https://acme.taleo.net/careersection/">', "taleo"),
    ],
)
def test_an_unsupported_platform_is_named_rather_than_shrugged_at(html, platform):
    """"Runs on Workday" is a worklist item; "unresolved" is not."""
    found = discover_in_html(html)
    assert found.supported is False
    assert found.other_ats == platform
    assert platform in found.note


def test_a_page_with_no_ats_says_so():
    found = discover_in_html("<html><body>Send your CV to jobs@example.com</body></html>")
    assert found.supported is False
    assert found.other_ats is None
    assert "no ATS link" in found.note


def test_empty_markup_is_not_a_crash():
    assert discover_in_html("").supported is False


# ---------------------------------------------------------------------------
# Token guessing, used only as a fallback
# ---------------------------------------------------------------------------
def test_candidates_lead_with_the_registrable_domain():
    # "protect.airbus.com" — the company is Airbus, not "protect".
    assert candidate_tokens("Airbus Protect", "https://www.protect.airbus.com")[0] == "airbus"


def test_candidates_drop_parentheticals_and_suffixes():
    assert "foxit" in candidate_tokens("Fox-IT (NCC Group)", "")
    assert "leonardo" in candidate_tokens("Leonardo S.p.A.", "")
    # The parenthetical company must not leak into the token.
    assert not any("ncc" in token for token in candidate_tokens("Fox-IT (NCC Group)", ""))


def test_candidates_handle_accents_and_ampersands():
    tokens = candidate_tokens("Rohde & Schwarz Cybersecurity", "")
    assert any("rohde" in token for token in tokens)
    assert all(token.isascii() for token in tokens)


def test_candidates_are_deduplicated():
    tokens = candidate_tokens("NVISO", "https://www.nviso.eu")
    assert len(tokens) == len(set(tokens))


# ---------------------------------------------------------------------------
# Reading the curated file
# ---------------------------------------------------------------------------
def test_load_organizations_reads_the_curated_shape(tmp_path):
    path = tmp_path / "companies.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": 8,
                    "organization": "NVISO",
                    "category": "Cybersecurity",
                    "website": "https://www.nviso.eu",
                    "careers_page": "https://www.nviso.eu/careers/",
                },
                {"organization": "  ", "category": "ignored"},
                "not an object",
            ]
        )
    )
    organizations = load_organizations(path)
    assert len(organizations) == 1
    assert organizations[0].organization == "NVISO"
    assert organizations[0].category == "Cybersecurity"


def test_load_organizations_rejects_a_non_array(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"organization": "Acme"}')
    with pytest.raises(ValueError, match="JSON array"):
        load_organizations(path)


# ---------------------------------------------------------------------------
# Resolving end to end
# ---------------------------------------------------------------------------
def _org(name, careers, website="") -> Organization:
    return Organization(organization=name, careers_page=careers, website=website)


@respx.mock
def test_resolution_verifies_the_board_before_trusting_the_page():
    respx.get("https://acme.example/careers").mock(
        return_value=httpx.Response(
            200, html='<iframe src="https://boards.greenhouse.io/embed/job_board?for=acme">'
        )
    )
    respx.get(f"{GREENHOUSE}/acme/jobs").mock(
        return_value=httpx.Response(200, json={"jobs": [{"id": 1}, {"id": 2}]})
    )

    report = resolve_all([_org("Acme", "https://acme.example/careers")], workers=1)
    resolution = report.resolutions[0]
    assert resolution.resolved
    assert (resolution.source_type, resolution.board_token) == ("greenhouse", "acme")
    assert resolution.job_count == 2


@respx.mock
def test_a_linked_board_that_does_not_answer_is_not_reported_as_resolved():
    """A stale link on a careers page must not become a 404-ing target."""
    respx.get("https://stale.example/careers").mock(
        return_value=httpx.Response(200, html='<a href="https://job-boards.greenhouse.io/gone">')
    )
    respx.get(f"{GREENHOUSE}/gone/jobs").mock(return_value=httpx.Response(404))

    report = resolve_all([_org("Stale", "https://stale.example/careers")], workers=1)
    assert report.resolutions[0].resolved is False
    assert "did not answer" in report.resolutions[0].detail


@respx.mock
def test_an_unsupported_platform_is_carried_into_the_report():
    respx.get("https://big.example/careers").mock(
        return_value=httpx.Response(200, html='<a href="https://x.myworkdayjobs.com/c">')
    )
    report = resolve_all([_org("Big Corp", "https://big.example/careers")], workers=1)
    assert report.unresolved[0].other_ats == "workday"
    assert report.by_other_ats() == {"workday": 1}


@respx.mock
def test_an_unreachable_careers_page_is_reported_not_fatal():
    respx.get("https://down.example/careers").mock(side_effect=httpx.ConnectError("refused"))
    respx.get("https://ok.example/careers").mock(
        return_value=httpx.Response(200, html='<a href="https://jobs.lever.co/ok">')
    )
    respx.get(f"{LEVER}/ok").mock(return_value=httpx.Response(200, json=[{"id": "a"}]))

    report = resolve_all(
        [_org("Down", "https://down.example/careers"), _org("OK", "https://ok.example/careers")],
        workers=1,
    )
    assert len(report.resolved) == 1
    assert "unreachable" in report.unresolved[0].detail


@respx.mock
def test_guessing_is_off_by_default_and_costs_nothing():
    respx.get("https://quiet.example/careers").mock(
        return_value=httpx.Response(200, html="<p>We are hiring</p>")
    )
    probe = respx.get(f"{GREENHOUSE}/quiet/jobs").mock(
        return_value=httpx.Response(200, json={"jobs": []})
    )
    report = resolve_all(
        [_org("Quiet", "https://quiet.example/careers", "https://quiet.example")], workers=1
    )
    assert report.resolved == []
    assert probe.call_count == 0, "guessing must be opt-in — it is other people's API"


@respx.mock
def test_guessing_finds_a_board_the_page_never_mentioned():
    respx.get("https://quiet.example/careers").mock(
        return_value=httpx.Response(200, html="<p>We are hiring</p>")
    )
    respx.get(f"{GREENHOUSE}/quiet/jobs").mock(return_value=httpx.Response(404))
    respx.get(f"{LEVER}/quiet").mock(return_value=httpx.Response(404))
    respx.get(f"{ASHBY}/quiet").mock(return_value=httpx.Response(200, json={"jobs": [{"id": "x"}]}))

    report = resolve_all(
        [_org("Quiet", "https://quiet.example/careers", "https://quiet.example")],
        guess=True,
        workers=1,
    )
    assert report.resolved[0].board_token == "quiet"
    assert report.resolved[0].source_type == "ashby"
    assert "guessed" in report.resolved[0].detail


@respx.mock
def test_the_report_serialises_with_a_summary():
    respx.get("https://a.example/careers").mock(
        return_value=httpx.Response(200, html='<a href="https://jobs.lever.co/a">')
    )
    respx.get(f"{LEVER}/a").mock(return_value=httpx.Response(200, json=[]))
    respx.get("https://b.example/careers").mock(
        return_value=httpx.Response(200, html='<a href="https://x.myworkdayjobs.com/c">')
    )

    report = resolve_all(
        [_org("A", "https://a.example/careers"), _org("B", "https://b.example/careers")],
        workers=1,
    )
    payload = json.loads(report.to_json())
    assert payload["summary"] == {
        "total": 2,
        "resolved": 1,
        "unresolved": 1,
        "unsupported_platforms": {"workday": 1},
    }
    assert payload["resolved"][0]["organization"] == "A"


# ---------------------------------------------------------------------------
# Merging into the configured targets
# ---------------------------------------------------------------------------
def _resolution(name: str, token: str):
    """One resolved lever board, built through the real resolver."""
    respx.get(f"https://{token}.example/careers").mock(
        return_value=httpx.Response(200, html=f'<a href="https://jobs.lever.co/{token}">')
    )
    respx.get(f"{LEVER}/{token}").mock(return_value=httpx.Response(200, json=[]))
    return resolve_all([_org(name, f"https://{token}.example/careers")], workers=1).resolved[0]


@respx.mock
def test_merging_keeps_existing_targets_untouched():
    """An operator may have fixed a token or disabled a company on purpose."""
    resolution = _resolution("Acme", "acme")
    existing = [
        TargetSource(company="Acme", source_type="lever", board_token="acme", enabled=False),
    ]
    merged = merge_targets(existing, [resolution])
    assert len(merged) == 1
    assert merged[0].enabled is False


@respx.mock
def test_merging_appends_only_what_is_new():
    resolution = _resolution("Newco", "newco")
    existing = [TargetSource(company="Old", source_type="greenhouse", board_token="old")]
    merged = merge_targets(existing, [resolution])
    assert [target.board_token for target in merged] == ["old", "newco"]


@respx.mock
def test_merging_is_idempotent():
    resolution = _resolution("Acme", "acme")
    once = merge_targets([], [resolution])
    twice = merge_targets(once, [resolution])
    assert len(twice) == 1


# ---------------------------------------------------------------------------
# CLI argument handling
# ---------------------------------------------------------------------------
def test_a_missing_file_explains_where_it_looked(tmp_path, capsys, monkeypatch):
    """"[Errno 2]" alone leaves the reader guessing which directory was used."""
    from acide.__main__ import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["acide", "import-companies", "companies.json"])

    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 1

    message = capsys.readouterr().err
    assert "no such file" in message
    assert str(tmp_path) in message, "it must say which directory it looked in"
    assert "full path" in message


def test_a_tilde_path_is_expanded(tmp_path, capsys, monkeypatch):
    """`~/Downloads/companies.json` is what people actually type."""
    from acide.__main__ import main

    home = tmp_path / "home"
    (home / "Downloads").mkdir(parents=True)
    (home / "Downloads" / "companies.json").write_text("[]")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("sys.argv", ["acide", "import-companies", "~/Downloads/companies.json"])

    with pytest.raises(SystemExit) as caught:
        main()
    # The file was found and read; it is simply empty, which is a different
    # failure from "no such file".
    assert "no such file" not in capsys.readouterr().err
    assert caught.value.code == 1


# ---------------------------------------------------------------------------
# A long browser run must survive being interrupted
# ---------------------------------------------------------------------------
def _fake_browser(monkeypatch, behaviour):
    """Stand in for Playwright so these tests need no browser.

    `behaviour(org)` returns a PageResult, or raises to simulate a crash.
    """
    from acide import browser_discovery

    class FakeSession:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return None

    def fake_resolve(session, organizations, *, on_result=None, on_log=None, **kwargs):
        results = []
        for org in organizations:
            result = behaviour(org)
            results.append((org, result))
            if on_result:
                on_result(org, result)
        return results

    monkeypatch.setattr(browser_discovery, "BrowserSession", FakeSession)
    monkeypatch.setattr(browser_discovery, "resolve_with_browser", fake_resolve)


def test_browser_resolutions_are_handed_over_as_each_page_finishes(monkeypatch):
    """Not in one batch at the end: 631 pages at human pace take over an hour."""
    from acide.browser_discovery import PageResult
    from acide.discovery import Discovery
    from acide.watchlist import resolve_all_with_browser

    _fake_browser(monkeypatch, lambda org: PageResult(Discovery(note="no supported ATS")))

    seen: list[str] = []
    organizations = [Organization(organization=name) for name in ("A", "B", "C")]
    report = resolve_all_with_browser(
        organizations, on_resolution=lambda r: seen.append(r.organization)
    )
    assert seen == ["A", "B", "C"], "each result must arrive as it happens"
    assert len(report.resolutions) == 3


def test_a_crash_part_way_still_leaves_the_finished_work_in_hand(monkeypatch):
    """A browser dying at organization 600 must not discard the first 599."""
    from acide.browser_discovery import PageResult
    from acide.discovery import Discovery
    from acide.watchlist import resolve_all_with_browser

    def behaviour(org):
        if org.organization == "Boom":
            raise RuntimeError("the browser died")
        return PageResult(Discovery(note="no supported ATS"))

    _fake_browser(monkeypatch, behaviour)

    seen: list[str] = []
    organizations = [Organization(organization=n) for n in ("A", "B", "Boom", "D")]
    with pytest.raises(RuntimeError):
        resolve_all_with_browser(
            organizations, on_resolution=lambda r: seen.append(r.organization)
        )
    assert seen == ["A", "B"], "everything finished before the crash is still good"


def test_an_interrupted_import_writes_a_report_that_resumes_the_remainder(
    tmp_path, monkeypatch, capsys
):
    """--retry-report must pick up the unvisited, not just the failures."""
    from acide.__main__ import main
    from acide.browser_discovery import PageResult
    from acide.discovery import Discovery
    from acide.watchlist import organizations_from_report

    def behaviour(org):
        if org.organization == "C":
            raise KeyboardInterrupt
        return PageResult(Discovery(note="no supported ATS"))

    _fake_browser(monkeypatch, behaviour)

    source = tmp_path / "companies.json"
    source.write_text(
        json.dumps(
            [
                {"organization": name, "website": f"https://{name.lower()}.example"}
                for name in ("A", "B", "C", "D", "E")
            ]
        )
    )
    report_path = tmp_path / "import-report.json"
    monkeypatch.setattr(
        "sys.argv",
        ["acide", "import-companies", str(source), "--browser", "--report", str(report_path)],
    )

    with pytest.raises(SystemExit) as caught:
        main()
    # Ctrl-C is a decision, not a crash: 130 and no traceback.
    assert caught.value.code == 130

    assert report_path.exists(), "an interrupted run must leave its progress behind"
    remaining = {org.organization for org in organizations_from_report(report_path)}
    # A and B were visited and found nothing, so they are retried too; C, D
    # and E were never reached and must not be silently dropped.
    assert {"C", "D", "E"} <= remaining
    assert "Resume with" in capsys.readouterr().err


def test_a_report_carries_the_website_so_a_retry_can_use_it(tmp_path):
    """fallback_urls needs the host; without it a retry has less to go on."""
    from acide.watchlist import ImportReport, Resolution, organizations_from_report

    path = tmp_path / "report.json"
    path.write_text(
        ImportReport(
            resolutions=[
                Resolution(
                    organization="Acme",
                    category="Cybersecurity",
                    website="https://acme.example",
                    careers_page="https://acme.example/careers",
                )
            ]
        ).to_json()
    )

    [org] = organizations_from_report(path)
    assert org.website == "https://acme.example"
    assert org.category == "Cybersecurity"


def test_shared_careers_pages_are_reported_for_review():
    """A group running one careers site is fine; unrelated bodies sharing a
    portal usually means nobody found their actual page."""
    from acide.__main__ import _shared_pages
    from acide.watchlist import Resolution

    resolutions = [
        Resolution(organization="Thales Group", careers_page="https://careers.thales/global"),
        Resolution(organization="Thales Alenia", careers_page="https://careers.thales/global"),
        Resolution(organization="CINEA", careers_page="https://eu-careers.europa.eu/en"),
        Resolution(organization="HaDEA", careers_page="https://eu-careers.europa.eu/en"),
        Resolution(organization="Acme", careers_page="https://acme.example/careers"),
        Resolution(organization="NoPage", careers_page=""),
    ]
    shared = _shared_pages(resolutions)
    assert set(shared) == {"https://careers.thales/global", "https://eu-careers.europa.eu/en"}
    assert shared["https://careers.thales/global"] == ["Thales Group", "Thales Alenia"]
    assert "https://acme.example/careers" not in shared, "one owner is not shared"

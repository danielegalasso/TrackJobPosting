"""Probing unresolved careers pages without a browser.

A browser pass over six hundred pages takes over an hour. Most of the question
— is there anything readable here at all — is answerable over plain HTTP in a
couple of minutes, and knowing the answer first is worth the two minutes.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from acide.probe import Probe, ProbeReport, probe_all, probe_one
from acide.watchlist import Organization


def _client() -> httpx.Client:
    return httpx.Client(follow_redirects=True, timeout=5.0)


def _ld(payload) -> str:
    return f'<script type="application/ld+json">{json.dumps(payload)}</script>'


POSTING = {
    "@type": "JobPosting",
    "title": "SOC Analyst",
    "description": "Watch things.",
    "datePosted": "2026-09-01",
}


@respx.mock
def test_a_page_linking_a_recognised_board_says_so():
    """Breezy arrived after earlier runs; this is how a page is re-judged."""
    respx.get("https://telespazio.be/en/careers").mock(return_value=httpx.Response(
        200, html='<a href="https://telespazio-be.breezy.hr/">Discover our positions here</a>'
    ))
    with _client() as client:
        result = probe_one(client, "Telespazio Belgium", "https://telespazio.be/en/careers")
    assert result.resolvable is True
    assert (result.source_type, result.board_token) == ("breezy", "telespazio-be")
    assert result.verdict == "links a board we can read"


@respx.mock
def test_a_page_publishing_its_own_postings_says_so():
    respx.get("https://acme.example/careers").mock(
        return_value=httpx.Response(200, html=_ld(POSTING))
    )
    with _client() as client:
        result = probe_one(client, "Acme", "https://acme.example/careers")
    assert result.verdict == "publishes its own postings"
    assert result.source_type == "jsonld"
    assert result.has_structured_postings is True


@respx.mock
def test_an_unsupported_platform_is_named():
    respx.get("https://acme.example/careers").mock(return_value=httpx.Response(
        200, html='<a href="https://career5.successfactors.eu/career">Apply</a>'
    ))
    with _client() as client:
        result = probe_one(client, "Acme", "https://acme.example/careers")
    assert result.verdict == "runs on successfactors"
    assert result.resolvable is False


@respx.mock
def test_a_javascript_only_page_is_the_one_that_needs_a_browser():
    """This is the residue a browser pass is actually for."""
    respx.get("https://acme.example/careers").mock(
        return_value=httpx.Response(200, html='<div id="board">Loading…</div>')
    )
    with _client() as client:
        result = probe_one(client, "Acme", "https://acme.example/careers")
    assert result.verdict == "needs a browser"
    assert result.has_structured_postings is False


@respx.mock
def test_a_dead_page_is_reported_not_fatal():
    respx.get("https://acme.example/careers").mock(return_value=httpx.Response(404))
    respx.get("https://gone.example/careers").mock(
        side_effect=httpx.ConnectError("no such host")
    )
    with _client() as client:
        assert probe_one(client, "Acme", "https://acme.example/careers").verdict == "HTTP 404"
        gone = probe_one(client, "Gone", "https://gone.example/careers")
    assert gone.verdict == "unreachable"
    assert "no such host" in gone.note


def test_a_page_that_was_never_given_is_not_fetched():
    with _client() as client:
        result = probe_one(client, "Acme", "")
    assert result.verdict == "nothing found"
    assert result.note == "no careers page given"


@respx.mock
def test_probe_all_summarises_the_whole_list():
    respx.get("https://a.example/careers").mock(return_value=httpx.Response(
        200, html='<a href="https://a-co.breezy.hr/">Positions</a>'
    ))
    respx.get("https://b.example/careers").mock(
        return_value=httpx.Response(200, html=_ld(POSTING))
    )
    respx.get("https://c.example/careers").mock(
        return_value=httpx.Response(200, html="<div>Loading…</div>")
    )

    organizations = [
        Organization(organization=name, careers_page=f"https://{letter}.example/careers")
        for name, letter in (("A", "a"), ("B", "b"), ("C", "c"))
    ]
    with _client() as client:
        report = probe_all(organizations, client=client, workers=1)

    assert len(report.probes) == 3
    assert report.by_source_type() == {"breezy": 1, "jsonld": 1}
    assert report.by_verdict()["needs a browser"] == 1
    assert "2 of 3 would resolve now" in report.summary_line()
    assert "1 publish their own" in report.summary_line()

    payload = json.loads(report.to_json())
    assert payload["summary"]["would_resolve_now"] == 2


def test_the_report_serialises_every_probe():
    report = ProbeReport(probes=[
        Probe(organization="A", careers_page="u", verdict="needs a browser"),
        Probe(organization="B", careers_page="v", verdict="links a board we can read",
              source_type="breezy", board_token="b"),
    ])
    payload = json.loads(report.to_json())
    assert len(payload["probes"]) == 2
    assert payload["summary"]["by_verdict"]["needs a browser"] == 1


# ---------------------------------------------------------------------------
# The CLI
# ---------------------------------------------------------------------------
@respx.mock
def test_probe_reads_an_import_report_and_writes_its_own(tmp_path, monkeypatch, capsys):
    from acide.__main__ import main

    respx.get("https://a.example/careers").mock(return_value=httpx.Response(
        200, html='<a href="https://a-co.breezy.hr/">Positions</a>'
    ))
    source = tmp_path / "import-report.json"
    source.write_text(json.dumps({
        "resolved": [],
        "unresolved": [
            {"organization": "A", "careers_page": "https://a.example/careers",
             "website": "https://a.example", "category": "Cybersecurity"}
        ],
    }))
    out = tmp_path / "probe.json"
    monkeypatch.setattr(
        "sys.argv", ["acide", "probe", str(source), "--report", str(out), "-v"]
    )

    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 0

    printed = capsys.readouterr().out
    assert "1 of 1 would resolve now" in printed
    assert "no board is contacted" in printed, "it must say it is a dry run"
    payload = json.loads(out.read_text())
    assert payload["probes"][0]["board_token"] == "a-co"


# ---------------------------------------------------------------------------
# A refused request is not a dead page
# ---------------------------------------------------------------------------
@respx.mock
@pytest.mark.parametrize("status", [403, 404, 429])
def test_a_refusal_is_not_reported_as_a_dead_page(status):
    """126 of a real 523-page probe answered 4xx on pages a browser had loaded.

    Plenty of sites refuse anything that is not a real browser, and some do it
    with 404 rather than 403 — which reads as "deleted" and is not.
    """
    respx.get("https://acme.example/careers").mock(return_value=httpx.Response(status))
    with _client() as client:
        result = probe_one(
            client, "Acme", "https://acme.example/careers",
            "no ATS link found on the page",
        )
    assert result.verdict == "refuses plain HTTP"
    assert "a browser loaded this page" in result.note
    assert str(status) in result.note


@respx.mock
def test_a_page_no_browser_ever_loaded_is_still_reported_as_dead():
    respx.get("https://acme.example/careers").mock(return_value=httpx.Response(404))
    with _client() as client:
        result = probe_one(
            client, "Acme", "https://acme.example/careers",
            "careers page returned HTTP 404",
        )
    assert result.verdict == "HTTP 404"


@respx.mock
def test_a_connection_failure_on_a_page_the_browser_loaded_is_a_refusal_too():
    respx.get("https://acme.example/careers").mock(
        side_effect=httpx.ConnectError("handshake failed")
    )
    with _client() as client:
        refused = probe_one(
            client, "Acme", "https://acme.example/careers", "runs on successfactors"
        )
        genuine = probe_one(client, "Acme", "https://acme.example/careers", "")
    assert refused.verdict == "refuses plain HTTP"
    assert genuine.verdict == "unreachable"


def test_the_summary_counts_refusals_separately():
    report = ProbeReport(probes=[
        Probe(organization="A", careers_page="u", verdict="refuses plain HTTP"),
        Probe(organization="B", careers_page="v", verdict="needs a browser"),
    ])
    assert "1 refused this plain request but load in a browser" in report.summary_line()


def test_a_report_carries_the_previous_detail_into_the_probe(tmp_path):
    """Without it the probe cannot tell the two cases apart at all."""
    from acide.watchlist import organizations_from_report

    path = tmp_path / "report.json"
    path.write_text(json.dumps({"resolved": [], "unresolved": [
        {"organization": "Acme", "careers_page": "https://acme.example/careers",
         "detail": "no ATS link found on the page"}
    ]}))
    [org] = organizations_from_report(path)
    assert org.previous_detail == "no ATS link found on the page"


# ---------------------------------------------------------------------------
# Adopting the pages nothing else can read
# ---------------------------------------------------------------------------
def test_only_pages_with_no_cheaper_answer_are_adopted():
    """A page that links a board should be re-imported, not rendered: an API
    is cheaper and stronger. A dead page has no answer at all."""
    from acide.probe import browser_targets

    report = ProbeReport(probes=[
        Probe(organization="Renders", careers_page="https://a/careers",
              verdict="needs a browser"),
        Probe(organization="Refuses", careers_page="https://b/careers",
              verdict="refuses plain HTTP"),
        Probe(organization="HasBoard", careers_page="https://c/careers",
              verdict="links a board we can read", source_type="breezy", board_token="c"),
        Probe(organization="Structured", careers_page="https://d/careers",
              verdict="publishes its own postings", source_type="jsonld", board_token="https://d"),
        Probe(organization="Dead", careers_page="https://e/careers", verdict="HTTP 404"),
        Probe(organization="Platform", careers_page="https://f/careers",
              verdict="runs on successfactors"),
        Probe(organization="NoUrl", careers_page="", verdict="needs a browser"),
    ])
    targets = list(browser_targets(report))
    assert [t.company for t in targets] == ["Renders", "Refuses"]
    assert all(t.source_type == "browser" for t in targets)
    assert targets[0].board_token == "https://a/careers"


def test_refusals_can_be_left_out():
    from acide.probe import browser_targets

    report = ProbeReport(probes=[
        Probe(organization="Renders", careers_page="https://a/c", verdict="needs a browser"),
        Probe(organization="Refuses", careers_page="https://b/c", verdict="refuses plain HTTP"),
    ])
    assert [t.company for t in browser_targets(report, include_refusals=False)] == ["Renders"]


def test_a_report_round_trips_through_its_own_json():
    from acide.probe import report_from_json

    original = ProbeReport(probes=[
        Probe(organization="A", careers_page="https://a/c", verdict="needs a browser",
              status=200, note="no ATS link found on the page"),
    ])
    back = report_from_json(json.loads(original.to_json()))
    assert back.probes == original.probes


def test_adopting_is_a_dry_run_until_apply(tmp_path, monkeypatch, capsys):
    from acide import config as config_module
    from acide.__main__ import main

    report = tmp_path / "probe.json"
    report.write_text(ProbeReport(probes=[
        Probe(organization="Spazio", careers_page="https://spazio.example/careers",
              verdict="needs a browser"),
    ]).to_json())

    monkeypatch.setattr("sys.argv", ["acide", "adopt-browser", str(report)])
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 0
    printed = capsys.readouterr().out
    assert "Spazio" in printed
    assert "Nothing was saved" in printed
    assert config_module.load(refresh=True).targets == []

    monkeypatch.setattr("sys.argv", ["acide", "adopt-browser", str(report), "--apply"])
    with pytest.raises(SystemExit):
        main()
    [target] = config_module.load(refresh=True).targets
    assert (target.company, target.source_type) == ("Spazio", "browser")
    assert target.board_token == "https://spazio.example/careers"


def test_adopting_twice_does_not_duplicate_a_target(tmp_path, monkeypatch):
    from acide import config as config_module
    from acide.__main__ import main

    report = tmp_path / "probe.json"
    report.write_text(ProbeReport(probes=[
        Probe(organization="Spazio", careers_page="https://spazio.example/careers",
              verdict="needs a browser"),
    ]).to_json())
    for _ in range(2):
        monkeypatch.setattr("sys.argv", ["acide", "adopt-browser", str(report), "--apply"])
        with pytest.raises(SystemExit):
            main()
    assert len(config_module.load(refresh=True).targets) == 1


def test_the_limit_is_respected(tmp_path, monkeypatch, capsys):
    from acide.__main__ import main

    report = tmp_path / "probe.json"
    report.write_text(ProbeReport(probes=[
        Probe(organization=f"Co {n}", careers_page=f"https://c{n}.example/careers",
              verdict="needs a browser")
        for n in range(10)
    ]).to_json())
    monkeypatch.setattr(
        "sys.argv", ["acide", "adopt-browser", str(report), "--limit", "3"]
    )
    with pytest.raises(SystemExit):
        main()
    assert "3 careers page(s)" in capsys.readouterr().out


def test_an_import_report_is_rejected_with_the_command_to_run(tmp_path, monkeypatch, capsys):
    """The two reports look similar enough to be confused."""
    from acide.__main__ import main

    wrong = tmp_path / "import-report.json"
    wrong.write_text(json.dumps({"resolved": [], "unresolved": []}))
    monkeypatch.setattr("sys.argv", ["acide", "adopt-browser", str(wrong)])
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 1
    assert "acide probe" in capsys.readouterr().err

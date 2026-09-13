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

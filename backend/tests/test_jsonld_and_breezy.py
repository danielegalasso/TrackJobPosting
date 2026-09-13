"""Reading postings from a page that names no ATS, and from Breezy.

"Page rendered, no ATS link" was 377 of a real 631-entry list — the largest
single failure by a wide margin. Two things account for a great deal of it: a
platform nobody recognised, and a page that publishes its own postings as
schema.org JobPosting because Google for Jobs reads that.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from acide.discovery import discover_in_html, has_job_posting_markup
from acide.models import TargetSource
from acide.spider import BreezyConnector, JsonLdConnector
from acide.spider.base import ConnectorError
from acide.spider.jsonld import blocks, walk


def _run(connector_class, token, *, terms=(), max_jobs=120, company="Acme"):
    with httpx.Client(follow_redirects=True, timeout=5.0) as client:
        connector = connector_class(
            client, request_delay=0, max_jobs=max_jobs, search_terms=terms
        )
        return list(connector.fetch(TargetSource(
            company=company, source_type=connector_class.source_type, board_token=token
        )))


def _ld(payload) -> str:
    return f'<script type="application/ld+json">{json.dumps(payload)}</script>'


# ---------------------------------------------------------------------------
# Telespazio Belgium, the case that prompted this
# ---------------------------------------------------------------------------
TELESPAZIO_CAREERS = """
<html><body>
  <h1>Careers at Telespazio Belgium</h1>
  <p>We make space possible.</p>
  <a class="cta" href="https://telespazio-be.breezy.hr/">Discover our positions here</a>
</body></html>
"""


def test_the_telespazio_careers_page_now_resolves_to_its_board():
    """Its careers page named no ATS only because Breezy was unrecognised.

    The button labelled "Discover our positions here" points straight at
    telespazio-be.breezy.hr, so nothing has to be clicked: the link is in the
    markup and Breezy is a board like any other.
    """
    found = discover_in_html(TELESPAZIO_CAREERS, "https://www.telespazio.be/en/careers")
    assert found.supported is True
    assert (found.source_type, found.board_token) == ("breezy", "telespazio-be")


@respx.mock
def test_breezy_reads_a_whole_board_in_one_request():
    respx.get("https://telespazio-be.breezy.hr/json").mock(
        return_value=httpx.Response(200, json=[
            {
                "id": "747f443f533301",
                "friendly_id": "747f443f533301-it-infrastructure-administrator",
                "name": "IT Infrastructure Administrator",
                "type": {"name": "Full-Time"},
                "location": {"city": "Bastogne", "country": {"name": "Belgium"}},
                "description": "<p>Keep the estate running.</p>",
                "published_date": "2026-08-11T00:00:00Z",
            },
            {
                "id": "6f5d350879c801",
                "name": "Data Intelligence Engineer",
                "location": {"city": "Noordwijk-Binnen", "country": {"name": "Netherlands"},
                             "is_remote": False},
                "description": "<p>Build pipelines.</p>",
                "published_date": "2026-07-02T00:00:00Z",
            },
        ])
    )
    first, second = _run(BreezyConnector, "telespazio-be", company="Telespazio Belgium")
    assert first.title == "IT Infrastructure Administrator"
    assert first.location == "Bastogne, Belgium"
    assert first.apply_url.endswith("/p/747f443f533301-it-infrastructure-administrator")
    assert first.date_posted == "2026-08-11"
    assert "estate running" in first.description
    assert second.location == "Noordwijk-Binnen, Netherlands"


@respx.mock
def test_breezy_respects_search_terms():
    respx.get("https://acme.breezy.hr/json").mock(return_value=httpx.Response(200, json=[
        {"id": "1", "name": "Cyber Analyst", "description": "x"},
        {"id": "2", "name": "Office Manager", "description": "x"},
    ]))
    postings = _run(BreezyConnector, "acme", terms=("cyber",))
    assert [p.title for p in postings] == ["Cyber Analyst"]


# ---------------------------------------------------------------------------
# A page that publishes its own postings
# ---------------------------------------------------------------------------
POSTING = {
    "@context": "https://schema.org",
    "@type": "JobPosting",
    "title": "Cyber Security Officer",
    "description": "<p>Defend the ground segment.</p>",
    "datePosted": "2026-09-01",
    "employmentType": "FULL_TIME",
    "identifier": {"@type": "PropertyValue", "name": "REQ", "value": "REQ-4471"},
    "url": "https://acme.example/jobs/cyber-security-officer",
    "hiringOrganization": {"@type": "Organization", "name": "Acme"},
    "jobLocation": {
        "@type": "Place",
        "address": {
            "@type": "PostalAddress",
            "addressLocality": "Darmstadt",
            "addressCountry": "DE",
        },
    },
    "baseSalary": {
        "@type": "MonetaryAmount",
        "currency": "EUR",
        "value": {"@type": "QuantitativeValue", "value": 68000, "unitText": "YEAR"},
    },
}


@respx.mock
def test_a_page_with_no_ats_still_yields_its_postings():
    respx.get("https://acme.example/careers").mock(
        return_value=httpx.Response(200, html=f"<html><body>{_ld(POSTING)}</body></html>")
    )
    [posting] = _run(JsonLdConnector, "https://acme.example/careers")
    assert posting.external_id == "REQ-4471"
    assert posting.title == "Cyber Security Officer"
    assert posting.location == "Darmstadt, DE"
    assert posting.apply_url == "https://acme.example/jobs/cyber-security-officer"
    assert posting.date_posted == "2026-09-01"
    assert "ground segment" in posting.description
    # A published salary is used directly rather than parsed out of prose.
    assert (posting.amount, posting.currency, posting.rate) == (68000.0, "EUR", "Yearly")


@pytest.mark.parametrize(
    "wrapper",
    [
        "bare",
        "list",
        "graph",
        "itemlist",
    ],
)
@respx.mock
def test_postings_are_found_however_the_page_nests_them(wrapper):
    payloads = {
        "bare": POSTING,
        "list": [POSTING],
        "graph": {"@context": "https://schema.org", "@graph": [POSTING]},
        "itemlist": {
            "@type": "ItemList",
            "itemListElement": [{"@type": "ListItem", "position": 1, "item": POSTING}],
        },
    }
    respx.get("https://acme.example/careers").mock(
        return_value=httpx.Response(200, html=_ld(payloads[wrapper]))
    )
    [posting] = _run(JsonLdConnector, "https://acme.example/careers")
    assert posting.title == "Cyber Security Officer"


@respx.mock
def test_an_index_page_is_followed_to_each_posting():
    """A listing often carries only an ItemList of links; the advert is a page down."""
    index = _ld({
        "@type": "ItemList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "url": "/jobs/one"},
            {"@type": "ListItem", "position": 2, "url": "/jobs/two"},
        ],
    })
    respx.get("https://acme.example/careers").mock(return_value=httpx.Response(200, html=index))
    for slug, title in (("one", "Ground Segment Engineer"), ("two", "SOC Analyst")):
        respx.get(f"https://acme.example/jobs/{slug}").mock(
            return_value=httpx.Response(200, html=_ld({**POSTING, "title": title}))
        )
    postings = _run(JsonLdConnector, "https://acme.example/careers")
    assert sorted(p.title for p in postings) == ["Ground Segment Engineer", "SOC Analyst"]


@respx.mock
def test_a_javascript_only_page_says_so_rather_than_guessing():
    respx.get("https://acme.example/careers").mock(
        return_value=httpx.Response(200, html="<div id=board>Loading…</div>")
    )
    with pytest.raises(ConnectorError, match="JavaScript"):
        _run(JsonLdConnector, "https://acme.example/careers")


@respx.mock
def test_one_broken_json_ld_block_does_not_lose_the_others():
    html = (
        '<script type="application/ld+json">{ not json at all</script>'
        + _ld(POSTING)
    )
    respx.get("https://acme.example/careers").mock(return_value=httpx.Response(200, html=html))
    assert len(_run(JsonLdConnector, "https://acme.example/careers")) == 1


@respx.mock
def test_a_remote_posting_without_an_address_says_remote():
    remote = {k: v for k, v in POSTING.items() if k != "jobLocation"}
    remote["jobLocationType"] = "TELECOMMUTE"
    respx.get("https://acme.example/careers").mock(
        return_value=httpx.Response(200, html=_ld(remote))
    )
    [posting] = _run(JsonLdConnector, "https://acme.example/careers")
    assert posting.location == "Remote"


# ---------------------------------------------------------------------------
# Discovery proposes a jsonld target for a page with no board
# ---------------------------------------------------------------------------
def test_a_page_publishing_its_own_postings_becomes_a_target():
    found = discover_in_html(_ld(POSTING), "https://acme.example/careers")
    assert found.supported is True
    assert (found.source_type, found.board_token) == ("jsonld", "https://acme.example/careers")


def test_a_real_board_still_wins_over_the_pages_own_markup():
    """An API is better than reading a page, so the board comes first."""
    html = _ld(POSTING) + '<a href="https://boards.greenhouse.io/acme">Jobs</a>'
    found = discover_in_html(html, "https://acme.example/careers")
    assert (found.source_type, found.board_token) == ("greenhouse", "acme")


def test_structured_postings_beat_naming_an_unsupported_platform():
    """"Runs on successfactors" is a worklist entry; this is readable today."""
    html = _ld(POSTING) + '<a href="https://career5.successfactors.eu/career">Apply</a>'
    found = discover_in_html(html, "https://acme.example/careers")
    assert found.source_type == "jsonld"


def test_a_page_with_no_structured_postings_is_not_proposed():
    found = discover_in_html("<h1>Careers</h1><p>Email us.</p>", "https://acme.example/careers")
    assert found.supported is False


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ("<h1>nothing here</h1>", False),
        # The cheap string test must not be fooled by the word alone.
        ("<p>we use JobPosting markup soon</p>", False),
        (_ld(POSTING), True),
        (_ld({"@type": "Organization", "name": "Acme"}), False),
    ],
)
def test_has_job_posting_markup(html, expected):
    assert has_job_posting_markup(html) is expected


def test_walk_and_blocks_survive_rubbish():
    assert list(blocks("<script type='application/ld+json'>[[[</script>")) == []
    assert list(walk(None)) == []
    assert list(walk({"@type": "Organization"})) == []

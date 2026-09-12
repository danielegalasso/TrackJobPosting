"""Checking careers URLs, and finding where a dead one moved to."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from acide.linkcheck import (
    Repair,
    careers_links,
    looks_like_careers_url,
    repair_all,
    repair_one,
)
from acide.watchlist import Organization


def _client() -> httpx.Client:
    return httpx.Client(follow_redirects=True, timeout=5.0)


# ---------------------------------------------------------------------------
# Reading a site's own navigation
# ---------------------------------------------------------------------------
def test_finds_the_careers_link_in_a_nav_bar():
    html = """
      <nav><a href="/about">About</a><a href="/careers">Careers</a>
      <a href="/contact">Contact</a></nav>
    """
    assert careers_links(html, "https://acme.com")[0] == "https://acme.com/careers"


@pytest.mark.parametrize(
    ("href", "label"),
    [
        ("/lavora-con-noi", "Lavora con noi"),
        ("/karriere", "Karriere"),
        ("/fr/carrieres", "Carrières"),
        ("/vacatures", "Vacatures"),
        ("/en/talent", "Talent"),
        ("/werken-bij", "Werken bij ons"),
    ],
)
def test_finds_careers_links_in_other_languages(href, label):
    """This kind of list is full of Italian, German, French and Dutch sites."""
    html = f'<nav><a href="/about">About</a><a href="{href}">{label}</a></nav>'
    assert careers_links(html, "https://acme.com")[0] == f"https://acme.com{href}"


def test_a_link_named_careers_counts_even_with_an_opaque_path():
    html = '<a href="/p/1234">Join our team</a><a href="/x">Other</a>'
    # "Join our team" is not matched, but "join-us" style paths and career
    # words in the text are; an opaque path with no career word is ignored.
    assert careers_links(html, "https://acme.com") == []

    html = '<a href="/p/1234">Careers</a>'
    assert careers_links(html, "https://acme.com") == ["https://acme.com/p/1234"]


def test_the_landing_page_outranks_an_individual_posting():
    html = """
      <a href="/careers/senior-security-engineer-1234">Senior Security Engineer</a>
      <a href="/careers">Careers</a>
    """
    assert careers_links(html, "https://acme.com")[0] == "https://acme.com/careers"


def test_the_companys_own_domain_outranks_a_third_party():
    html = """
      <a href="https://www.linkedin.com/company/acme/jobs/">Jobs on LinkedIn</a>
      <a href="https://acme.com/jobs">Jobs</a>
    """
    assert careers_links(html, "https://acme.com")[0] == "https://acme.com/jobs"


def test_non_navigable_links_are_ignored():
    html = '<a href="mailto:jobs@acme.com">jobs@acme.com</a><a href="tel:+390123">Call</a>'
    assert careers_links(html, "https://acme.com") == []


def test_no_links_at_all_is_not_a_crash():
    assert careers_links("", "https://acme.com") == []


# ---------------------------------------------------------------------------
# Checking and repairing one organization
# ---------------------------------------------------------------------------
@respx.mock
def test_a_working_url_is_left_alone():
    respx.get("https://acme.com/careers").mock(return_value=httpx.Response(200, html="<h1>Jobs</h1>"))
    with _client() as client:
        result = repair_one(client, organization="Acme", careers_page="https://acme.com/careers")
    assert result.verdict == "working"
    assert result.suggested == ""


@respx.mock
def test_a_redirect_is_taken_as_the_sites_own_answer():
    respx.get("https://acme.com/jobs").mock(
        return_value=httpx.Response(301, headers={"Location": "https://acme.com/careers"})
    )
    respx.get("https://acme.com/careers").mock(return_value=httpx.Response(200, html="<h1>Jobs</h1>"))
    with _client() as client:
        result = repair_one(client, organization="Acme", careers_page="https://acme.com/jobs")
    assert result.verdict == "moved"
    assert result.suggested == "https://acme.com/careers"
    assert "redirect" in result.how


@respx.mock
def test_a_404_is_repaired_from_the_sites_navigation():
    """The 136 dead links in a real list mostly moved, not disappeared."""
    respx.get("https://acme.com/old-careers").mock(return_value=httpx.Response(404))
    respx.get("https://acme.com").mock(
        return_value=httpx.Response(
            200, html='<nav><a href="/lavora-con-noi">Lavora con noi</a></nav>'
        )
    )
    respx.get("https://acme.com/lavora-con-noi").mock(
        return_value=httpx.Response(200, html="<h1>Posizioni aperte</h1>")
    )

    with _client() as client:
        result = repair_one(
            client,
            organization="Acme",
            careers_page="https://acme.com/old-careers",
            website="https://acme.com",
        )
    assert result.verdict == "repaired"
    assert result.suggested == "https://acme.com/lavora-con-noi"
    assert "navigation" in result.how


@respx.mock
def test_a_conventional_path_is_only_tried_when_the_nav_gives_nothing():
    respx.get("https://acme.com/old").mock(return_value=httpx.Response(404))
    respx.get("https://acme.com").mock(return_value=httpx.Response(200, html="<h1>Acme</h1>"))
    respx.get("https://acme.com/careers").mock(return_value=httpx.Response(200, html="<h1>Jobs</h1>"))

    with _client() as client:
        result = repair_one(
            client,
            organization="Acme",
            careers_page="https://acme.com/old",
            website="https://acme.com",
        )
    assert result.verdict == "repaired"
    assert result.suggested == "https://acme.com/careers"
    assert "guessed" in result.how


@respx.mock
def test_a_candidate_is_never_proposed_without_being_checked():
    """A suggestion that 404s would be worse than none at all."""

    def only_the_homepage(request):
        # The homepage lists a careers link, but every candidate is dead.
        if request.url.path in ("", "/"):
            return httpx.Response(200, html='<a href="/careers">Careers</a>')
        return httpx.Response(404)

    respx.route(host="acme.com").mock(side_effect=only_the_homepage)

    with _client() as client:
        result = repair_one(
            client,
            organization="Acme",
            careers_page="https://acme.com/old",
            website="https://acme.com",
        )
    assert result.verdict == "broken"
    assert result.suggested == ""


@respx.mock
def test_a_dead_domain_is_reported_as_broken():
    respx.get("https://gone.example/careers").mock(side_effect=httpx.ConnectError("no such host"))
    respx.get("https://gone.example").mock(side_effect=httpx.ConnectError("no such host"))
    with _client() as client:
        result = repair_one(
            client,
            organization="Gone",
            careers_page="https://gone.example/careers",
            website="https://gone.example",
        )
    assert result.verdict == "broken"
    assert "homepage also unreachable" in result.note


@respx.mock
def test_a_403_is_treated_as_reachable_enough_to_repair_from():
    """A WAF refusing a script does not mean the careers page moved."""
    respx.get("https://acme.com/careers").mock(return_value=httpx.Response(403))
    respx.get("https://acme.com").mock(return_value=httpx.Response(403))
    with _client() as client:
        result = repair_one(
            client,
            organization="Acme",
            careers_page="https://acme.com/careers",
            website="https://acme.com",
        )
    # Nothing can be verified, so nothing is proposed — but it is recorded.
    assert result.verdict == "broken"
    assert "403" in result.note


# ---------------------------------------------------------------------------
# Whole-list behaviour
# ---------------------------------------------------------------------------
@respx.mock
def test_repair_all_reports_every_organization():
    respx.get("https://a.example/careers").mock(return_value=httpx.Response(200, html="ok"))
    respx.get("https://b.example/careers").mock(return_value=httpx.Response(404))
    respx.get("https://b.example").mock(
        return_value=httpx.Response(200, html='<a href="/jobs">Jobs</a>')
    )
    respx.get("https://b.example/jobs").mock(return_value=httpx.Response(200, html="ok"))

    organizations = [
        Organization(organization="A", careers_page="https://a.example/careers",
                     website="https://a.example"),
        Organization(organization="B", careers_page="https://b.example/careers",
                     website="https://b.example"),
    ]
    with _client() as client:
        repairs = repair_all(organizations, client=client, workers=1)

    assert [r.verdict for r in repairs] == ["working", "repaired"]
    assert repairs[1].suggested == "https://b.example/jobs"


def test_needs_attention_flags_everything_but_working():
    assert Repair(organization="x", verdict="working").needs_attention is False
    for verdict in ("moved", "repaired", "broken"):
        assert Repair(organization="x", verdict=verdict).needs_attention is True


# ---------------------------------------------------------------------------
# A redirect is only as good as where it lands
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://careers.thalesgroup.com/global/en", True),   # signal in the host
        ("https://www.nviso.eu/jobs", True),                   # signal in the path
        ("https://acme.com/lavora_con_noi", True),             # underscores normalise
        ("https://acme.com/", False),                          # a site root, never careers
        ("https://acme.com", False),
        ("https://acme.com/en", False),
    ],
)
def test_looks_like_careers_url(url, expected):
    assert looks_like_careers_url(url) is expected


@respx.mock
def test_a_redirect_to_the_homepage_is_not_taken_as_the_careers_page():
    """Retiring /careers by pointing it at / is common, and means "gone".

    Taking it at face value would replace a merely stale URL with a
    definitely wrong one, and then send a browser to index a homepage.
    """
    respx.get("https://acme.com/careers").mock(
        return_value=httpx.Response(301, headers={"Location": "https://acme.com/"})
    )
    respx.get("https://acme.com/").mock(
        return_value=httpx.Response(200, html='<nav><a href="/work-with-us">Work with us</a></nav>')
    )
    respx.get("https://acme.com").mock(
        return_value=httpx.Response(200, html='<nav><a href="/work-with-us">Work with us</a></nav>')
    )
    respx.get("https://acme.com/work-with-us").mock(
        return_value=httpx.Response(200, html="<h1>Open roles</h1>")
    )

    with _client() as client:
        result = repair_one(
            client,
            organization="Acme",
            careers_page="https://acme.com/careers",
            website="https://acme.com",
        )
    assert result.verdict == "repaired"
    assert result.suggested == "https://acme.com/work-with-us"
    assert "navigation" in result.how


@respx.mock
def test_a_redirect_into_a_careers_host_is_still_trusted():
    """careers.thalesgroup.com → /global/en keeps the signal in the host."""
    # The specific route is registered first: respx matches a bare host
    # against every path on it, which would make this redirect a loop.
    respx.get("https://careers.acme.com/global/en").mock(
        return_value=httpx.Response(200, html="<h1>Jobs</h1>")
    )
    respx.get("https://careers.acme.com").mock(
        return_value=httpx.Response(302, headers={"Location": "https://careers.acme.com/global/en"})
    )
    with _client() as client:
        result = repair_one(
            client, organization="Acme", careers_page="https://careers.acme.com"
        )
    assert result.verdict == "moved"
    assert result.suggested == "https://careers.acme.com/global/en"
    assert result.how == "followed a redirect"


@respx.mock
def test_a_homepage_redirect_is_kept_only_when_nothing_better_exists():
    """Still better than nothing: the site sent us there and it answers."""

    def homepage_only(request):
        if request.url.path in ("", "/"):
            return httpx.Response(200, html="<h1>Acme</h1>")
        if request.url.path == "/careers":
            return httpx.Response(301, headers={"Location": "https://acme.com/"})
        return httpx.Response(404)

    respx.route(host="acme.com").mock(side_effect=homepage_only)

    with _client() as client:
        result = repair_one(
            client,
            organization="Acme",
            careers_page="https://acme.com/careers",
            website="https://acme.com",
        )
    assert result.verdict == "moved"
    assert result.suggested == "https://acme.com/"
    assert "no careers link found there" in result.how


# ---------------------------------------------------------------------------
# Rewriting the list
# ---------------------------------------------------------------------------
@respx.mock
def test_the_corrected_list_keeps_fields_the_checker_does_not_own(tmp_path, monkeypatch):
    """A curated list carries more than this tool understands.

    Rebuilding each entry from the four fields it knows silently dropped the
    operator's own — an id, a note — on every run.
    """
    from acide.__main__ import main

    respx.get("https://acme.example/careers").mock(
        return_value=httpx.Response(200, html="<h1>Jobs</h1>")
    )
    respx.get("https://beta.example/old").mock(return_value=httpx.Response(404))
    respx.get("https://beta.example/jobs").mock(
        return_value=httpx.Response(200, html="<h1>Jobs</h1>")
    )
    respx.get("https://beta.example").mock(
        return_value=httpx.Response(200, html='<a href="/jobs">Jobs</a>')
    )

    source = tmp_path / "companies.json"
    source.write_text(
        json.dumps(
            [
                {
                    "id": 7,
                    "organization": "Acme",
                    "category": "Cybersecurity",
                    "website": "https://acme.example",
                    "careers_page": "https://acme.example/careers",
                    "notes": "met them at a conference",
                },
                {
                    "id": 8,
                    "organization": "Beta",
                    "website": "https://beta.example",
                    "careers_page": "https://beta.example/old",
                },
            ]
        )
    )
    fixed = tmp_path / "companies.fixed.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "acide", "check-urls", str(source),
            "--write", str(fixed),
            "--report", str(tmp_path / "report.json"),
        ],
    )

    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 0

    kept, repaired = json.loads(fixed.read_text())
    assert kept["id"] == 7
    assert kept["notes"] == "met them at a conference"
    assert kept["careers_page"] == "https://acme.example/careers"
    # The one field the checker does own is still rewritten.
    assert repaired["id"] == 8
    assert repaired["careers_page"] == "https://beta.example/jobs"

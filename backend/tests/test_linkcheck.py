"""Checking careers URLs, and finding where a dead one moved to."""

from __future__ import annotations

import httpx
import pytest
import respx

from acide.linkcheck import Repair, careers_links, repair_all, repair_one
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

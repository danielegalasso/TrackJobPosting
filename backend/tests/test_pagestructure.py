"""Finding the job list on a page nobody wrote a connector for.

About 250 organizations on a real list render their roles behind an interface
of their own. The list still has a shape: every posting shares a URL prefix and
differs in its last segment. A navigation bar shares a shape too, which is why
the link text has to be judged as well.
"""

from __future__ import annotations

import json

import pytest

from acide.pagestructure import (
    PostingLink,
    meta_tags,
    posting_links,
    read_posting,
    text_of,
)

# The real structure of a Breezy-hosted list: each role is linked three times
# over — by title, by location, by contract type — which is exactly the trap a
# naive link scrape falls into.
TELESPAZIO = """
<html><body>
<nav>
  <a href="/en/">Home</a><a href="/en/about">About us</a>
  <a href="/en/contact">Contact</a><a href="/en/news">News</a>
</nav>
<h2>Le nostre posizioni</h2>
<ul>
  <li>
    <a href="/p/747f443f533301-it-infrastructure-administrator">IT Infrastructure Administrator</a>
    <a href="/p/747f443f533301-it-infrastructure-administrator">Bastogne, LX</a>
    <a href="/p/747f443f533301-it-infrastructure-administrator">Tempo pieno</a>
  </li>
  <li>
    <a href="/p/6f5d350879c801-data-intelligence-engineer">Data Intelligence Engineer</a>
    <a href="/p/6f5d350879c801-data-intelligence-engineer">Noordwijk-Binnen, NL</a>
  </li>
  <li>
    <a href="/p/a85e757abb0e01-gnc-aocs-sensors-and-actuators-engineer">GNC-AOCS Sensors and Actuators Engineer</a>
    <a href="/p/a85e757abb0e01-gnc-aocs-sensors-and-actuators-engineer">Noordwijk-Binnen, NL</a>
  </li>
  <li>
    <a href="/p/fe297ac1787d01-safety-and-certification-engineer">Safety and Certification Engineer</a>
    <a href="/p/fe297ac1787d01-safety-and-certification-engineer">Toulouse, FR</a>
  </li>
</ul>
<footer><a href="/en/privacy">Privacy</a><a href="/en/terms">Terms</a></footer>
</body></html>
"""


def test_the_job_list_is_found_and_the_navigation_is_not():
    links = posting_links(TELESPAZIO, "https://telespazio-be.breezy.hr/")
    assert len(links) == 4, [link.text for link in links]
    assert all("/p/" in link.url for link in links)
    assert not any("about" in link.url or "privacy" in link.url for link in links)


def test_a_role_linked_several_times_appears_once_under_its_title():
    """The title is the longest of the three texts, which is the useful one."""
    links = posting_links(TELESPAZIO, "https://telespazio-be.breezy.hr/")
    administrator = [
        link for link in links if link.url.endswith("it-infrastructure-administrator")
    ]
    assert len(administrator) == 1
    assert administrator[0].text == "IT Infrastructure Administrator"


def test_a_navigation_bar_alone_is_not_a_job_list():
    """Four sibling links of one word each are chrome, whatever their shape."""
    html = """<nav>
      <a href="/en/about">About</a><a href="/en/contact">Contact</a>
      <a href="/en/news">News</a><a href="/en/team">Team</a>
    </nav>"""
    assert posting_links(html, "https://acme.example/careers") == []


def test_a_job_shaped_prefix_outranks_a_larger_group_of_articles():
    """A newsroom can be longer than the job list and must not win."""
    html = "".join(
        f'<a href="/news/{n}-some-company-announcement-number-{n}">'
        f"Some company announcement number {n}</a>"
        for n in range(12)
    ) + "".join(
        f'<a href="/careers/jobs/{n}-security-engineer-role">Security Engineer {n}</a>'
        for n in range(4)
    )
    links = posting_links(html, "https://acme.example/careers")
    assert links, "nothing was found at all"
    assert all("/careers/jobs/" in link.url for link in links)


def test_a_single_posting_is_not_a_list():
    html = '<a href="/careers/jobs/1-security-engineer">Security Engineer</a>'
    assert posting_links(html, "https://acme.example/careers") == []


def test_the_page_itself_is_never_one_of_its_own_postings():
    html = """
      <a href="https://acme.example/careers">Careers</a>
      <a href="/careers/jobs/1-cyber-analyst">Cyber Analyst</a>
      <a href="/careers/jobs/2-soc-engineer">SOC Engineer</a>
    """
    links = posting_links(html, "https://acme.example/careers")
    assert all(link.url.rstrip("/") != "https://acme.example/careers" for link in links)


def test_non_navigable_links_are_ignored():
    html = """
      <a href="mailto:jobs@acme.com">jobs@acme.com</a>
      <a href="tel:+390123">Call us today</a>
      <a href="javascript:void(0)">Open the menu</a>
      <a href="#main">Skip to main content</a>
    """
    assert posting_links(html, "https://acme.example/careers") == []


def test_no_html_is_not_a_crash():
    assert posting_links("", "https://acme.example/careers") == []
    assert posting_links("<html></html>", "https://acme.example/careers") == []


def test_a_posting_link_is_hashable_so_it_can_be_de_duplicated():
    assert len({PostingLink("u", "a"), PostingLink("u", "a")}) == 1


# ---------------------------------------------------------------------------
# Reading one posting page
# ---------------------------------------------------------------------------
def test_structured_data_on_a_posting_page_is_preferred():
    """A posting page carries JobPosting far more often than a landing page."""
    payload = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": "Cyber Defence Analyst",
        "description": "<p>Watch the estate.</p>",
        "datePosted": "2026-09-01",
        "jobLocation": {"@type": "Place", "address": {
            "@type": "PostalAddress", "addressLocality": "Rome", "addressCountry": "IT"}},
        "baseSalary": {"@type": "MonetaryAmount", "currency": "EUR",
                       "value": {"@type": "QuantitativeValue", "value": 55000,
                                 "unitText": "YEAR"}},
    }
    html = (
        "<html><head><title>Careers | Acme</title></head><body>"
        f'<script type="application/ld+json">{json.dumps(payload)}</script>'
        "<h1>Something else entirely</h1></body></html>"
    )
    facts = read_posting(html, "https://acme.example/jobs/1")
    assert facts.source == "schema.org JobPosting"
    assert facts.title == "Cyber Defence Analyst"
    assert facts.location == "Rome, IT"
    assert (facts.amount, facts.currency, facts.rate) == (55000.0, "EUR", "Yearly")
    assert "estate" in facts.description


def test_without_structured_data_the_heading_is_used_and_labelled_as_a_guess():
    html = """
      <html><head>
        <title>SOC Analyst | Acme Careers</title>
        <meta property="og:description" content="Join our detection team.">
      </head><body>
        <nav><a href="/">Home</a></nav>
        <h1>SOC Analyst</h1>
        <p>You will triage alerts and tune detections.</p>
        <footer>© Acme</footer>
      </body></html>
    """
    facts = read_posting(html, "https://acme.example/jobs/2")
    assert facts.title == "SOC Analyst"
    assert facts.source == "the page's heading"
    assert "triage alerts" in facts.description
    # Chrome is stripped from the body text.
    assert "Home" not in facts.description
    assert "© Acme" not in facts.description


def test_a_site_name_is_stripped_from_a_page_title():
    html = "<html><head><title>Threat Intelligence Lead | Acme</title></head><body></body></html>"
    facts = read_posting(html, "https://acme.example/jobs/3")
    assert facts.title == "Threat Intelligence Lead"


def test_a_page_with_no_title_at_all_is_not_usable():
    facts = read_posting("<html><body><p>nothing</p></body></html>", "https://acme.example/x")
    assert facts.usable is False


@pytest.mark.parametrize(
    "html",
    [
        '<meta property="og:title" content="Cyber Analyst">',
        '<meta content="Cyber Analyst" property="og:title">',
        "<META NAME='og:title' CONTENT='Cyber Analyst'>",
    ],
)
def test_meta_tags_are_read_in_either_attribute_order(html):
    assert meta_tags(html)["og:title"] == "Cyber Analyst"


def test_text_of_unescapes_and_collapses():
    assert text_of("<b>Senior</b>&nbsp;&amp;   Lead\n Engineer") == "Senior & Lead Engineer"


@pytest.mark.parametrize(
    ("prefix", "expected"),
    [
        ("p", True),                    # Breezy
        ("o", True),                    # Recruitee
        ("careers/jobs", True),
        ("en/vacatures", True),
        ("de/stellenangebote", True),
        ("job-openings", True),
        ("news", False),
        ("company", False),             # contains "p" — must not count
        ("press", False),               # contains "p" — must not count
        ("products/platform", False),
        ("", False),
    ],
)
def test_a_prefix_is_job_shaped_by_whole_segments_not_substrings(prefix, expected):
    """`p` is a real posting prefix; testing for it inside a segment would make
    "company" and "press" job-shaped, and a press release is not a role."""
    from acide.pagestructure import _is_job_prefix

    assert _is_job_prefix(prefix) is expected


def test_without_any_job_shaped_path_the_largest_plausible_group_is_used():
    """Some sites post at /o/123 or /r/123; size is the only signal left."""
    html = "".join(
        f'<a href="/x/{n}-senior-detection-engineer">Senior Detection Engineer {n}</a>'
        for n in range(5)
    ) + '<a href="/y/1-about-the-team">About the team page</a>'
    links = posting_links(html, "https://acme.example/careers")
    assert len(links) == 5
    assert all("/x/" in link.url for link in links)

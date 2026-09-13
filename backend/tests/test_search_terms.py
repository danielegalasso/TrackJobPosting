"""Narrowing a corporate board to the roles worth evaluating.

Thales, Airbus, Accenture and Booz Allen each publish around two thousand
roles worldwide. Without terms, max_jobs_per_source spends itself on an
arbitrary slice and the evaluator is billed for every one of them.
"""

from __future__ import annotations

import json as json_module

import httpx
import pytest
import respx

from acide.models import TargetSource
from acide.spider import (
    GreenhouseConnector,
    TeamtailorConnector,
    WorkdayConnector,
)

GREENHOUSE = "https://boards-api.greenhouse.io/v1/boards"


def _run(connector_class, token, *, terms=(), max_jobs=120, company="Acme"):
    logs: list[tuple[str, str]] = []
    with httpx.Client(follow_redirects=True, timeout=5.0) as client:
        connector = connector_class(
            client,
            request_delay=0,
            max_jobs=max_jobs,
            search_terms=terms,
            on_log=lambda message, level: logs.append((level, message)),
        )
        postings = list(connector.fetch(TargetSource(
            company=company, source_type=connector_class.source_type, board_token=token
        )))
    return postings, logs


# ---------------------------------------------------------------------------
# The matcher itself
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("terms", "title", "kept"),
    [
        ((), "Warehouse Operative", True),               # no terms: keep all
        (("cyber",), "Cyber Defence Analyst", True),
        (("cyber",), "Cybersecurity Engineer", True),    # substring, not word
        (("cyber",), "Warehouse Operative", False),
        (("cyber", "security"), "Security Architect", True),
        (("CYBER",), "cyber analyst", True),             # case-insensitive both ways
        (("", "  "), "Anything", True),                  # blank terms are ignored
    ],
)
def test_wanted_matches_case_insensitively_on_substrings(terms, title, kept):
    with httpx.Client() as client:
        connector = GreenhouseConnector(client, search_terms=terms)
        assert connector.wanted(title) is kept


# ---------------------------------------------------------------------------
# A board without a server-side search is filtered locally
# ---------------------------------------------------------------------------
@respx.mock
def test_a_plain_board_keeps_only_matching_titles():
    respx.get(f"{GREENHOUSE}/acme/jobs").mock(return_value=httpx.Response(200, json={"jobs": [
        {"id": 1, "title": "Cyber Threat Analyst", "content": "<p>Hunt.</p>",
         "absolute_url": "https://x/1", "location": {"name": "Rome"}},
        {"id": 2, "title": "Warehouse Operative", "content": "<p>Lift.</p>",
         "absolute_url": "https://x/2", "location": {"name": "Rome"}},
        {"id": 3, "title": "Security Engineer", "content": "<p>Defend.</p>",
         "absolute_url": "https://x/3", "location": {"name": "Rome"}},
    ]}))
    postings, _ = _run(GreenhouseConnector, "acme", terms=("cyber", "security"))
    assert [p.title for p in postings] == ["Cyber Threat Analyst", "Security Engineer"]


@respx.mock
def test_no_terms_means_every_posting():
    respx.get(f"{GREENHOUSE}/acme/jobs").mock(return_value=httpx.Response(200, json={"jobs": [
        {"id": 1, "title": "Cyber Threat Analyst", "content": "x", "absolute_url": "u"},
        {"id": 2, "title": "Warehouse Operative", "content": "x", "absolute_url": "u"},
    ]}))
    postings, _ = _run(GreenhouseConnector, "acme")
    assert len(postings) == 2


@respx.mock
def test_the_cap_applies_to_matches_not_to_the_raw_board():
    """Filtering before slicing is the point: otherwise a cap of 2 on a board
    whose first two roles are irrelevant yields nothing at all."""
    respx.get(f"{GREENHOUSE}/acme/jobs").mock(return_value=httpx.Response(200, json={"jobs": [
        {"id": 1, "title": "Warehouse Operative", "content": "x", "absolute_url": "u"},
        {"id": 2, "title": "Bus Driver", "content": "x", "absolute_url": "u"},
        {"id": 3, "title": "Cyber Analyst", "content": "x", "absolute_url": "u"},
    ]}))
    postings, _ = _run(GreenhouseConnector, "acme", terms=("cyber",), max_jobs=2)
    assert [p.title for p in postings] == ["Cyber Analyst"]


@respx.mock
def test_a_board_larger_than_the_cap_says_so():
    """Silently indexing 2 of 2,000 looks like a board with odd postings."""
    respx.get(f"{GREENHOUSE}/acme/jobs").mock(return_value=httpx.Response(200, json={"jobs": [
        {"id": n, "title": f"Role {n}", "content": "x", "absolute_url": "u"} for n in range(5)
    ]}))
    _, logs = _run(GreenhouseConnector, "acme", max_jobs=2)
    warnings = [message for level, message in logs if level == "warning"]
    assert warnings and "5 postings listed, indexing 2" in warnings[0]
    assert "search_terms" in warnings[0], "it must say what to do about it"


# ---------------------------------------------------------------------------
# Workday narrows server-side, which is what makes Thales usable
# ---------------------------------------------------------------------------
@respx.mock
def test_workday_asks_the_board_to_search_instead_of_walking_it():
    """The whole point: two requests, not a hundred pages of 2,000 roles."""
    seen: list[str] = []

    def listing(request):
        body = json_module.loads(request.content)
        seen.append(body["searchText"])
        if body["offset"] > 0:
            return httpx.Response(200, json={"total": 1, "jobPostings": []})
        return httpx.Response(200, json={"total": 1, "jobPostings": [{
            "title": f"{body['searchText'].title()} Specialist",
            "externalPath": f"/job/{body['searchText']}",
            "bulletFields": [f"R-{body['searchText']}"],
        }]})

    respx.post(
        "https://thales.wd3.myworkdayjobs.com/wday/cxs/thales/Careers/jobs"
    ).mock(side_effect=listing)
    respx.get(url__regex=r"https://thales\.wd3\.myworkdayjobs\.com/wday/cxs/thales/Careers/job/.*").mock(
        return_value=httpx.Response(200, json={"jobPostingInfo": {
            "jobDescription": "<p>Work.</p>", "startDate": "2026-01-01",
        }})
    )

    postings, _ = _run(
        WorkdayConnector,
        "thales.wd3.myworkdayjobs.com/Careers",
        terms=("cyber", "security"),
        company="Thales",
    )
    # One request per term, and no second page: a short page ends the walk.
    assert seen == ["cyber", "security"], seen
    assert sorted(p.title for p in postings) == ["Cyber Specialist", "Security Specialist"]


@respx.mock
def test_workday_without_terms_still_lists_the_whole_board():
    def listing(request):
        body = json_module.loads(request.content)
        assert body["searchText"] == ""
        if body["offset"] > 0:
            return httpx.Response(200, json={"jobPostings": []})
        return httpx.Response(200, json={"jobPostings": [
            {"title": "Anything", "externalPath": "", "bulletFields": ["R-1"]}
        ]})

    respx.post("https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/careers/jobs").mock(
        side_effect=listing
    )
    postings, _ = _run(WorkdayConnector, "acme.wd1.myworkdayjobs.com/careers")
    assert [p.title for p in postings] == ["Anything"]


@respx.mock
def test_workday_does_not_double_count_a_role_matching_two_terms():
    def listing(request):
        body = json_module.loads(request.content)
        if body["offset"] > 0:
            return httpx.Response(200, json={"jobPostings": []})
        # Both searches return the same role, as a real board would.
        return httpx.Response(200, json={"jobPostings": [{
            "title": "Cyber Security Lead",
            "externalPath": "/job/lead",
            "bulletFields": ["R-9"],
        }]})

    respx.post("https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/careers/jobs").mock(
        side_effect=listing
    )
    respx.get(
        "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/careers/job/lead"
    ).mock(return_value=httpx.Response(200, json={"jobPostingInfo": {"jobDescription": "x"}}))

    postings, _ = _run(
        WorkdayConnector, "acme.wd1.myworkdayjobs.com/careers", terms=("cyber", "security")
    )
    assert len(postings) == 1


# ---------------------------------------------------------------------------
# A per-target list overrides the global one
# ---------------------------------------------------------------------------
def test_a_target_can_carry_its_own_terms():
    target = TargetSource(
        company="Acme", source_type="teamtailor", board_token="acme",
        search_terms=["quantum"],
    )
    assert target.search_terms == ["quantum"]
    # And the default is empty, so existing setup.json files are unaffected.
    plain = TargetSource(company="B", source_type="teamtailor", board_token="b")
    assert plain.search_terms == []


@respx.mock
def test_teamtailor_rss_is_filtered_too():
    feed = """<?xml version="1.0"?><rss version="2.0"><channel>
      <item><title>Cyber Analyst</title><link>https://a/1</link></item>
      <item><title>Office Manager</title><link>https://a/2</link></item>
    </channel></rss>"""
    respx.get("https://acme.teamtailor.com/jobs.rss").mock(
        return_value=httpx.Response(200, text=feed)
    )
    postings, _ = _run(TeamtailorConnector, "acme", terms=("cyber",))
    assert [p.title for p in postings] == ["Cyber Analyst"]

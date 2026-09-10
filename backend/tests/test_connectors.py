"""ATS connectors, exercised against recorded board responses.

The network policy in CI blocks outbound calls to the real boards, so each
connector is driven through `respx` against a captured payload shape.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from acide.models import TargetSource
from acide.spider.ashby import AshbyConnector
from acide.spider.base import ConnectorError, iso_date, strip_html
from acide.spider.greenhouse import GreenhouseConnector
from acide.spider.lever import LeverConnector

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str):
    return json.loads((FIXTURES / name).read_text("utf-8"))


@pytest.fixture
def client():
    with httpx.Client(timeout=5.0) as http_client:
        yield http_client


def _connector(cls, client):
    # request_delay is the politeness floor in production; tests must not
    # actually sleep, so it is driven to zero here.
    return cls(client, request_delay=0.0, max_jobs=50)


@respx.mock
def test_greenhouse_maps_postings(client):
    respx.get("https://boards-api.greenhouse.io/v1/boards/examplecorp/jobs").mock(
        return_value=httpx.Response(200, json=_fixture("greenhouse_jobs.json"))
    )
    target = TargetSource(company="ExampleCorp", source_type="greenhouse", board_token="examplecorp")
    postings = list(_connector(GreenhouseConnector, client).fetch(target))

    assert len(postings) == 2
    first = postings[0]
    assert first.title == "Software Engineer, Watch Software"
    assert first.company == "ExampleCorp"
    assert first.external_id == "4512301"
    assert first.apply_url.endswith("/jobs/4512301")
    assert first.date_posted == "2026-09-08"
    # Two extra offices beyond the primary location.
    assert first.location == "Cupertino, California, United States +2 more"
    assert "RTOS experience" in first.description
    assert "<p>" not in first.description
    assert first.amount == pytest.approx(165_000)
    assert first.currency == "USD"

    second = postings[1]
    assert second.location == "Remote - United States"
    assert second.amount is None  # no pay published


@respx.mock
def test_lever_maps_postings(client):
    respx.get("https://api.lever.co/v0/postings/examplelever").mock(
        return_value=httpx.Response(200, json=_fixture("lever_postings.json"))
    )
    target = TargetSource(company="ExampleLever", source_type="lever", board_token="examplelever")
    postings = list(_connector(LeverConnector, client).fetch(target))

    assert len(postings) == 1
    posting = postings[0]
    assert posting.title == "Senior Backend Engineer"
    assert posting.location == "Rome, Italy +1 more"
    assert posting.apply_url == "https://jobs.lever.co/examplelever/9f3c1a7e"
    assert posting.date_posted == "2026-09-07"  # epoch millis
    assert "Kubernetes" in posting.description  # list sections are folded in
    assert posting.currency == "EUR"
    assert posting.amount == pytest.approx(85_000)


@respx.mock
def test_ashby_prefers_structured_compensation(client):
    respx.get("https://api.ashbyhq.com/posting-api/job-board/exampleashby").mock(
        return_value=httpx.Response(200, json=_fixture("ashby_board.json"))
    )
    target = TargetSource(company="ExampleAshby", source_type="ashby", board_token="exampleashby")
    postings = list(_connector(AshbyConnector, client).fetch(target))

    posting = postings[0]
    assert posting.title == "Machine Learning Engineer"
    assert posting.location == "London, United Kingdom +1 more"
    # The published band wins over anything parsed from prose.
    assert posting.amount == pytest.approx(95_000)
    assert posting.currency == "GBP"
    assert posting.rate == "Yearly"


@respx.mock
def test_max_jobs_caps_a_large_board(client):
    payload = {"jobs": [
        {
            "id": index,
            "title": f"Role {index}",
            "absolute_url": f"https://example.com/{index}",
            "location": {"name": "Remote"},
            "content": "",
        }
        for index in range(50)
    ]}
    respx.get("https://boards-api.greenhouse.io/v1/boards/big/jobs").mock(
        return_value=httpx.Response(200, json=payload)
    )
    target = TargetSource(company="Big", source_type="greenhouse", board_token="big")
    connector = GreenhouseConnector(client, request_delay=0.0, max_jobs=10)
    assert len(list(connector.fetch(target))) == 10


@respx.mock
@pytest.mark.parametrize(
    ("status", "needle"),
    [(404, "board not found"), (429, "rate limited"), (500, "HTTP 500")],
)
def test_source_errors_are_reported_not_raised_blindly(client, status, needle):
    respx.get("https://boards-api.greenhouse.io/v1/boards/missing/jobs").mock(
        return_value=httpx.Response(status)
    )
    target = TargetSource(company="Missing", source_type="greenhouse", board_token="missing")
    with pytest.raises(ConnectorError, match=needle):
        list(_connector(GreenhouseConnector, client).fetch(target))


def test_strip_html_produces_readable_text():
    text = strip_html("<p>Hello</p><ul><li>One</li><li>Two &amp; more</li></ul>")
    assert "Hello" in text
    assert "• One" in text
    assert "Two & more" in text
    assert "<" not in text


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-09-08T14:02:11-04:00", "2026-09-08"),
        (1788739200000, "2026-09-07"),
        ("2026-09-07T11:30:00.000Z", "2026-09-07"),
        ("", None),
        (None, None),
        ("not a date", None),
    ],
)
def test_iso_date_normalises_ats_shapes(value, expected):
    assert iso_date(value) == expected


def test_every_connector_registers_on_package_import():
    """Regression guard: the registry is built by import side effect.

    A production process imports `acide.spider` and nothing else, so if the
    connector modules are not imported there the registry is empty and every
    configured target fails with "unknown source type".
    """
    import importlib

    import acide.spider as spider_package

    importlib.reload(spider_package)
    assert set(spider_package.CONNECTORS) == {"greenhouse", "lever", "ashby"}
    for source_type in ("greenhouse", "lever", "ashby"):
        assert spider_package.get_connector(source_type).source_type == source_type

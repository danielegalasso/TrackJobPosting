"""The six feeds added after the original three.

Every payload here is shaped from the provider's own documentation. None of
these can be exercised against the live APIs from CI, so the tests pin the
shape and the edges — pagination caps, missing adverts, XML instead of JSON —
rather than claiming to prove the endpoints answer.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from acide.models import TargetSource
from acide.spider import (
    PersonioConnector,
    RecruiteeConnector,
    SmartRecruitersConnector,
    TeamtailorConnector,
    WorkableConnector,
    WorkdayConnector,
)
from acide.spider.base import ConnectorError
from acide.spider.teamtailor import feed_url
from acide.spider.workday import parse_board


def _run(connector_class, token, company="Acme"):
    with httpx.Client(follow_redirects=True, timeout=5.0) as client:
        connector = connector_class(client, request_delay=0)
        return list(connector.fetch(TargetSource(
            company=company, source_type=connector_class.source_type, board_token=token
        )))


# ---------------------------------------------------------------------------
# Recruitee
# ---------------------------------------------------------------------------
@respx.mock
def test_recruitee_reads_the_careers_site_feed():
    respx.get("https://acme.recruitee.com/api/offers/").mock(
        return_value=httpx.Response(200, json={"offers": [{
            "id": 771,
            "title": "Security Engineer",
            "description": "<p>You will defend things.</p>",
            "requirements": "<ul><li>Five years</li></ul>",
            "city": "Amsterdam",
            "country": "Netherlands",
            "careers_apply_url": "https://acme.recruitee.com/o/security-engineer",
            "created_at": "2026-08-01T09:00:00.000Z",
        }]})
    )
    [posting] = _run(RecruiteeConnector, "acme")
    assert posting.title == "Security Engineer"
    assert posting.location == "Amsterdam, Netherlands"
    assert posting.apply_url == "https://acme.recruitee.com/o/security-engineer"
    assert posting.date_posted == "2026-08-01"
    # The advert is split across two fields and the evaluator needs both.
    assert "defend things" in posting.description
    assert "Five years" in posting.description


@respx.mock
def test_recruitee_remote_without_a_city_says_remote():
    respx.get("https://acme.recruitee.com/api/offers/").mock(
        return_value=httpx.Response(200, json={"offers": [
            {"id": 1, "title": "Analyst", "remote": True, "description": "x"}
        ]})
    )
    [posting] = _run(RecruiteeConnector, "acme")
    assert posting.location == "Remote"


# ---------------------------------------------------------------------------
# Workable
# ---------------------------------------------------------------------------
@respx.mock
def test_workable_reads_the_widget_feed():
    respx.get("https://apply.workable.com/api/v1/widget/accounts/acme").mock(
        return_value=httpx.Response(200, json={"name": "Acme", "jobs": [{
            "title": "Threat Analyst",
            "shortcode": "A1B2C3",
            "city": "Berlin",
            "country": "Germany",
            "telecommuting": True,
            "application_url": "https://apply.workable.com/acme/j/A1B2C3/",
            "published_on": "2026-07-15",
            "description": "<p>Hunt things.</p>",
            "requirements": "<p>Curiosity.</p>",
            "benefits": "<p>Coffee.</p>",
        }]})
    )
    [posting] = _run(WorkableConnector, "acme")
    assert posting.external_id == "A1B2C3"
    assert posting.location.startswith("Remote")
    assert "Berlin" in posting.location
    assert all(word in posting.description for word in ("Hunt", "Curiosity", "Coffee"))


# ---------------------------------------------------------------------------
# Teamtailor — the documented RSS feed
# ---------------------------------------------------------------------------
TEAMTAILOR_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Jobs at Acme</title>
  <item>
    <title>Penetration Tester - Stockholm</title>
    <link>https://acme.teamtailor.com/jobs/9001-penetration-tester</link>
    <description>&lt;p&gt;Break in, politely.&lt;/p&gt;</description>
    <pubDate>Tue, 02 Jun 2026 08:00:00 +0200</pubDate>
    <guid>https://acme.teamtailor.com/jobs/9001-penetration-tester</guid>
  </item>
  <item>
    <title>Head of Engineering - Platform</title>
    <link>https://acme.teamtailor.com/jobs/9002-head-of-engineering</link>
    <description>&lt;p&gt;Lead the team.&lt;/p&gt;</description>
    <pubDate>Mon, 01 Jun 2026 08:00:00 +0200</pubDate>
  </item>
</channel></rss>
"""


@respx.mock
def test_teamtailor_reads_the_documented_rss_feed():
    """jobs.json is repeated everywhere and does not exist.

    A real run found ten Teamtailor tenants through it and every one refused;
    `.rss` is what Teamtailor's own documentation describes.
    """
    route = respx.get("https://acme.teamtailor.com/jobs.rss").mock(
        return_value=httpx.Response(200, text=TEAMTAILOR_RSS)
    )
    first, second = _run(TeamtailorConnector, "acme")
    assert route.called
    assert first.external_id == "9001-penetration-tester"
    assert first.title == "Penetration Tester - Stockholm"
    assert first.apply_url == "https://acme.teamtailor.com/jobs/9001-penetration-tester"
    assert first.date_posted == "2026-06-02"
    assert "Break in" in first.description
    # Titles are left whole and no location is inferred from them. "Role -
    # City" is common enough to tempt, but "Head of Engineering - Platform"
    # is not a place, and the portal filters on location: a guess would both
    # show nonsense and match nothing.
    assert second.title == "Head of Engineering - Platform"
    assert first.location == "Not specified"
    assert second.location == "Not specified"


@respx.mock
def test_teamtailor_uses_a_location_element_when_the_feed_has_one():
    feed = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0" xmlns:tt="https://example.invalid/tt"><channel>
      <item>
        <title>SOC Analyst</title>
        <link>https://acme.teamtailor.com/jobs/1</link>
        <tt:location>Rotterdam</tt:location>
      </item>
    </channel></rss>
    """
    respx.get("https://acme.teamtailor.com/jobs.rss").mock(
        return_value=httpx.Response(200, text=feed)
    )
    [posting] = _run(TeamtailorConnector, "acme")
    assert posting.location == "Rotterdam"


@respx.mock
def test_teamtailor_asks_for_a_full_page():
    route = respx.get("https://acme.teamtailor.com/jobs.rss").mock(
        return_value=httpx.Response(200, text=TEAMTAILOR_RSS)
    )
    _run(TeamtailorConnector, "acme")
    assert "per_page=200" in str(route.calls[0].request.url)


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("acme", "https://acme.teamtailor.com/jobs.rss"),
        # Many career sites run on the employer's own domain.
        ("careers.sateliot.com", "https://careers.sateliot.com/jobs.rss"),
        ("https://aerospace-jobs.sener/", "https://aerospace-jobs.sener/jobs.rss"),
    ],
)
def test_teamtailor_accepts_a_tenant_or_a_custom_host(token, expected):
    assert feed_url(token) == expected


@respx.mock
def test_teamtailor_a_site_without_a_feed_says_what_to_check():
    respx.get("https://acme.teamtailor.com/jobs.rss").mock(return_value=httpx.Response(404))
    with pytest.raises(ConnectorError, match="career-site host"):
        _run(TeamtailorConnector, "acme")


@respx.mock
def test_teamtailor_malformed_rss_says_so():
    respx.get("https://acme.teamtailor.com/jobs.rss").mock(
        return_value=httpx.Response(200, text="<rss><channel><item>")
    )
    with pytest.raises(ConnectorError, match="not valid RSS"):
        _run(TeamtailorConnector, "acme")


# ---------------------------------------------------------------------------
# Personio — XML, and two possible hosts
# ---------------------------------------------------------------------------
PERSONIO_XML = """<?xml version="1.0" encoding="UTF-8"?>
<workzag-jobs>
  <position>
    <id>4455</id>
    <office>Munich</office>
    <department>Security</department>
    <name>Incident Responder</name>
    <jobDescriptions>
      <jobDescription><name>Your tasks</name><value><![CDATA[<p>Respond.</p>]]></value></jobDescription>
      <jobDescription><name>Your profile</name><value><![CDATA[<p>Calm.</p>]]></value></jobDescription>
    </jobDescriptions>
    <employmentType>permanent</employmentType>
    <createdAt>2026-05-20T00:00:00+02:00</createdAt>
  </position>
</workzag-jobs>
"""


@respx.mock
def test_personio_parses_the_recruiting_xml():
    respx.get("https://acme.jobs.personio.de/xml").mock(
        return_value=httpx.Response(200, text=PERSONIO_XML)
    )
    [posting] = _run(PersonioConnector, "acme")
    assert posting.external_id == "4455"
    assert posting.title == "Incident Responder"
    assert posting.location == "Munich"
    assert posting.apply_url == "https://acme.jobs.personio.de/job/4455"
    assert posting.date_posted == "2026-05-20"
    # Both named blocks, with their headings, reach the evaluator.
    assert "Your tasks" in posting.description
    assert "Respond." in posting.description
    assert "Calm." in posting.description


@respx.mock
def test_personio_falls_back_to_the_com_host():
    """Some tenants are served from .com rather than .de."""
    respx.get("https://acme.jobs.personio.de/xml").mock(return_value=httpx.Response(404))
    respx.get("https://acme.jobs.personio.com/xml").mock(
        return_value=httpx.Response(200, text=PERSONIO_XML)
    )
    [posting] = _run(PersonioConnector, "acme")
    assert posting.apply_url == "https://acme.jobs.personio.com/job/4455"


@respx.mock
def test_personio_a_dead_tenant_is_an_error_not_silence():
    respx.get("https://acme.jobs.personio.de/xml").mock(return_value=httpx.Response(404))
    respx.get("https://acme.jobs.personio.com/xml").mock(return_value=httpx.Response(404))
    with pytest.raises(ConnectorError, match="404"):
        _run(PersonioConnector, "acme")


@respx.mock
def test_personio_malformed_xml_says_so():
    respx.get("https://acme.jobs.personio.de/xml").mock(
        return_value=httpx.Response(200, text="<workzag-jobs><position>")
    )
    with pytest.raises(ConnectorError, match="not valid XML"):
        _run(PersonioConnector, "acme")


# ---------------------------------------------------------------------------
# SmartRecruiters — the list has no advert, so details are fetched
# ---------------------------------------------------------------------------
@respx.mock
def test_smartrecruiters_joins_the_list_to_each_advert():
    respx.get("https://api.smartrecruiters.com/v1/companies/Acme/postings").mock(
        return_value=httpx.Response(200, json={"totalFound": 1, "content": [{
            "id": "743999",
            "name": "Cloud Security Architect",
            "releasedDate": "2026-04-10T12:00:00.000Z",
            "location": {"city": "Madrid", "country": "es", "remote": False},
        }]})
    )
    respx.get("https://api.smartrecruiters.com/v1/companies/Acme/postings/743999").mock(
        return_value=httpx.Response(200, json={"jobAd": {"sections": {
            "jobDescription": {"text": "<p>Design controls.</p>"},
            "qualifications": {"text": "<p>Ten years.</p>"},
        }}})
    )
    [posting] = _run(SmartRecruitersConnector, "Acme")
    assert posting.title == "Cloud Security Architect"
    assert posting.location == "Madrid, es"
    assert "Design controls" in posting.description
    assert "Ten years" in posting.description


@respx.mock
def test_smartrecruiters_a_posting_without_an_advert_is_still_reported():
    """Losing the title too would be worse than a missing description."""
    respx.get("https://api.smartrecruiters.com/v1/companies/Acme/postings").mock(
        return_value=httpx.Response(200, json={"content": [
            {"id": "1", "name": "Analyst", "location": {"city": "Rome"}}
        ]})
    )
    respx.get("https://api.smartrecruiters.com/v1/companies/Acme/postings/1").mock(
        return_value=httpx.Response(500)
    )
    [posting] = _run(SmartRecruitersConnector, "Acme")
    assert posting.title == "Analyst"
    assert posting.description == ""


# ---------------------------------------------------------------------------
# Workday — POST, a hard page cap, and a token carrying two values
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("token", "host", "tenant", "site"),
    [
        ("nxp.wd3.myworkdayjobs.com/careers", "nxp.wd3.myworkdayjobs.com", "nxp", "careers"),
        ("https://nxp.wd3.myworkdayjobs.com/en-US/careers", "nxp.wd3.myworkdayjobs.com", "nxp", "careers"),
        ("darktrace.wd5.myworkdayjobs.com/Darktrace", "darktrace.wd5.myworkdayjobs.com", "darktrace", "Darktrace"),
    ],
)
def test_workday_token_parsing(token, host, tenant, site):
    board = parse_board(token)
    assert (board.host, board.tenant, board.site) == (host, tenant, site)
    assert board.jobs_url == f"https://{host}/wday/cxs/{tenant}/{site}/jobs"


def test_workday_a_token_missing_the_site_is_rejected_clearly():
    with pytest.raises(ConnectorError, match="nxp.wd3.myworkdayjobs.com/careers"):
        parse_board("nxp.wd3.myworkdayjobs.com")


@respx.mock
def test_workday_posts_for_its_listing_and_fetches_each_advert():
    jobs = respx.post(
        "https://nxp.wd3.myworkdayjobs.com/wday/cxs/nxp/careers/jobs"
    ).mock(return_value=httpx.Response(200, json={"total": 1, "jobPostings": [{
        "title": "Firmware Security Engineer",
        "externalPath": "/job/Eindhoven/Firmware-Security-Engineer_R-10023",
        "locationsText": "Eindhoven, Netherlands",
        "bulletFields": ["R-10023"],
    }]}))
    respx.get(
        "https://nxp.wd3.myworkdayjobs.com/wday/cxs/nxp/careers"
        "/job/Eindhoven/Firmware-Security-Engineer_R-10023"
    ).mock(return_value=httpx.Response(200, json={"jobPostingInfo": {
        "jobDescription": "<p>Secure the boot chain.</p>",
        "startDate": "2026-03-03",
        "externalUrl": "https://nxp.wd3.myworkdayjobs.com/careers/job/R-10023",
    }}))

    [posting] = _run(WorkdayConnector, "nxp.wd3.myworkdayjobs.com/careers", company="NXP")
    assert posting.external_id == "R-10023"
    assert posting.title == "Firmware Security Engineer"
    assert posting.location == "Eindhoven, Netherlands"
    assert "boot chain" in posting.description
    assert posting.date_posted == "2026-03-03"
    # The listing really was a POST, which is the whole trick.
    assert jobs.calls[0].request.method == "POST"


@respx.mock
def test_workday_never_asks_for_more_than_twenty_at_a_time():
    """Above 20 Workday returns an empty page with no error at all."""
    import json as json_module

    seen_limits: list[int] = []

    def listing(request):
        body = json_module.loads(request.content)
        seen_limits.append(body["limit"])
        offset = body["offset"]
        if offset >= 25:
            return httpx.Response(200, json={"total": 25, "jobPostings": []})
        count = min(20, 25 - offset)
        return httpx.Response(200, json={"total": 25, "jobPostings": [
            {"title": f"Role {offset + n}", "externalPath": "", "bulletFields": [f"R-{offset + n}"]}
            for n in range(count)
        ]})

    respx.post("https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/careers/jobs").mock(
        side_effect=listing
    )
    postings = _run(WorkdayConnector, "acme.wd1.myworkdayjobs.com/careers")
    assert seen_limits and all(limit <= 20 for limit in seen_limits)
    assert len(postings) == 25, "it must page rather than stop at the cap"


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        # A tenant resolves to exactly one host.
        ("acme", ["https://acme.teamtailor.com/jobs.rss"]),
        # A custom domain does not. A real run proposed the company's own
        # marketing host, which serves no feed; www is dropped and the two
        # conventional career hosts are tried after it.
        (
            "www.exotrail.com",
            [
                "https://exotrail.com/jobs.rss",
                "https://careers.exotrail.com/jobs.rss",
                "https://jobs.exotrail.com/jobs.rss",
            ],
        ),
        (
            "careers.sateliot.com",
            [
                "https://careers.sateliot.com/jobs.rss",
                "https://careers.careers.sateliot.com/jobs.rss",
                "https://jobs.careers.sateliot.com/jobs.rss",
            ],
        ),
        ("https://leaf.space/", [
            "https://leaf.space/jobs.rss",
            "https://careers.leaf.space/jobs.rss",
            "https://jobs.leaf.space/jobs.rss",
        ]),
    ],
)
def test_teamtailor_tries_the_plausible_career_hosts(token, expected):
    from acide.spider.teamtailor import feed_urls

    assert feed_urls(token) == expected


@respx.mock
def test_teamtailor_falls_through_to_a_careers_subdomain():
    """www.exotrail.com serves no feed; careers.exotrail.com does."""
    respx.get("https://exotrail.com/jobs.rss").mock(return_value=httpx.Response(404))
    respx.get("https://careers.exotrail.com/jobs.rss").mock(
        return_value=httpx.Response(200, text=TEAMTAILOR_RSS)
    )
    postings = _run(TeamtailorConnector, "www.exotrail.com")
    assert len(postings) == 2


@respx.mock
def test_teamtailor_reports_the_failure_when_no_host_serves_a_feed():
    for host in ("acme.example", "careers.acme.example", "jobs.acme.example"):
        respx.get(f"https://{host}/jobs.rss").mock(return_value=httpx.Response(404))
    with pytest.raises(ConnectorError, match="career-site host"):
        _run(TeamtailorConnector, "acme.example")

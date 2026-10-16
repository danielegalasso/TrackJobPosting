"""Priority: the rules' ranking, and the model's review against the brief."""

from __future__ import annotations

import json
from datetime import date

import pytest

from acide import db, paths, ranking
from acide.ranking import _literal, _Terms, organization, score_posting


def _insert(job_id: str, title: str, company: str, **fields) -> None:
    row = {
        "location": "Brussels, Belgium",
        "description": "",
        "deadline": None,
        "seniority": "",
        **fields,
    }
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO jobs (id, external_id, source_type, title, company, location,
                              seniority, description, deadline, apply_url)
            VALUES (?, ?, 'extract', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                job_id,
                title,
                company,
                row["location"],
                row["seniority"],
                row["description"],
                row["deadline"],
                f"https://example.org/{job_id}",
            ),
        )


def _flags(verdict) -> list[str]:
    return [flag.text for flag in verdict.flags]


# ---------------------------------------------------------------------------
# Organizations
# ---------------------------------------------------------------------------
def test_the_specific_entry_wins_over_the_group():
    assert organization("Thales Alenia Space") == (1, "space")
    assert organization("Thales") == (1, "defence")
    assert organization("Telespazio (Leonardo)") == (1, "space")
    assert organization("Leonardo Cyber & Security Solutions") == (1, "defence")


def test_an_unknown_employer_is_tier_three():
    assert organization("Some Cyber Startup") == (3, "cyber")


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------
def test_the_ideal_posting_outranks_the_same_role_at_a_small_firm():
    big = score_posting(title="Cybersecurity Graduate", company="Airbus", location="Toulouse, France")
    small = score_posting(title="Cybersecurity Graduate", company="Tiny Cyber", location="Toulouse")
    assert big.score > small.score
    assert "Livello: graduate / trainee" in big.reasons


def test_iris_named_in_the_advert_puts_the_role_in_space():
    verdict = score_posting(
        title="Information Security Analyst, IRIS2",
        company="SES",
        location="Betzdorf, LU",
        description="Join the IRIS² programme security team.",
    )
    assert "IRIS²" in verdict.reasons
    assert verdict.sector == "space"


def test_a_sales_role_is_not_rescued_by_the_name():
    sales = score_posting(title="Account Executive, Defence", company="Airbus")
    analyst = score_posting(title="Cyber Threat Intelligence Analyst", company="Tiny Cyber")
    assert sales.score < analyst.score
    assert "Ruolo commerciale, amministrativo o di supporto" in _flags(sales)


def test_a_senior_role_sinks_and_says_why():
    junior = score_posting(title="Junior Security Engineer", company="Thales")
    senior = score_posting(title="Senior Security Engineer", company="Thales")
    asks_years = score_posting(
        title="Security Engineer",
        company="Thales",
        description="You have 7+ years of experience in network security.",
    )
    assert senior.score < junior.score / 1.5
    assert "Senior" in _flags(senior)
    assert "Senior (7+ anni)" in _flags(asks_years)


def test_us_citizenship_blocks_the_posting():
    verdict = score_posting(
        title="Cyber Engineer",
        company="Lockheed Martin",
        location="Fort Worth, TX",
        description="Applicants must be a U.S. citizen.",
    )
    assert verdict.blocked
    assert verdict.score < 25


def test_a_us_role_with_a_clearance_blocks_without_saying_citizen():
    # Lockheed's adverts say "classified" and "clearance", never "citizen".
    verdict = score_posting(
        title="Classified Cyber Security Asc - E1",
        company="Lockheed Martin",
        location="King of Prussia, PA",
        description="Supports classified programs; requires a clearance.",
    )
    assert "Non candidabile: ruolo USA con clearance" in _flags(verdict)


def test_a_country_code_is_not_read_as_a_us_state():
    # "DE" ends a GMV location as Germany, not Delaware.
    verdict = score_posting(title="Security Engineer", company="GMV", location="Darmstadt, DE")
    assert "Fuori da UE/SEE/Svizzera: serve un visto" not in _flags(verdict)
    assert verdict.excluded == ""


@pytest.mark.parametrize(
    "location",
    ["Austin, TX", "Bengaluru, India", "Belgrade, Serbia", "Istanbul"],
)
def test_where_an_italian_needs_a_visa_the_posting_is_excluded(location):
    verdict = score_posting(title="Security Engineer", company="Thales", location=location)
    assert verdict.excluded == ranking.EXCLUDED_VISA


@pytest.mark.parametrize("location", ["Zurich, Switzerland", "Oslo, Norway", "Toulouse, France"])
def test_the_eu_the_eea_and_switzerland_need_no_visa(location):
    verdict = score_posting(title="Security Engineer", company="Thales", location=location)
    assert verdict.excluded == ""


def test_a_role_offered_in_europe_and_abroad_needs_no_visa():
    # Revolut: "Barcelona; Dubai; Krakow" can be taken in Barcelona.
    verdict = score_posting(
        title="Security Engineer", company="Revolut", location="Barcelona (office); Dubai (office); Krakow (office)"
    )
    assert verdict.excluded == ""
    assert score_posting(title="Security Engineer", company="Revolut", location="Dubai; Riyadh").excluded == (
        ranking.EXCLUDED_VISA
    )


def test_a_uk_role_is_neither_excluded_nor_marked_down():
    # UK employers sponsor the visa readily: only a note remains.
    london = score_posting(title="Security Engineer", company="Thales", location="London, United Kingdom")
    paris = score_posting(title="Security Engineer", company="Thales", location="Paris, France")
    assert london.excluded == "" and london.score == paris.score
    assert "Regno Unito: visto, di solito sponsorizzato" in _flags(london)


def test_an_international_organisation_needs_no_visa_abroad():
    # NATO staff in Izmir or Norfolk work under NATO's own status.
    verdict = score_posting(title="Cyber Analyst", company="NATO", location="Türkiye-Izmir")
    assert verdict.excluded == ""
    assert "Organizzazione internazionale: niente visto" in _flags(verdict)


def test_senior_support_and_blocked_postings_are_excluded():
    assert score_posting(title="Senior Security Engineer", company="Thales").excluded == "Senior"
    assert (
        score_posting(title="Account Executive", company="Airbus").excluded
        == ranking.EXCLUDED_SUPPORT
    )
    blocked = score_posting(
        title="Cyber Engineer", company="Thales", description="Applicants must be a U.S. citizen."
    )
    assert blocked.excluded == ranking.EXCLUDED_NOT_ELIGIBLE


def test_physical_security_is_not_cyber():
    physical = score_posting(title="Junior Sicurezza Fisica", company="Leonardo")
    cyber = score_posting(title="Junior Cyber Security Engineer", company="Leonardo")
    assert physical.score < cyber.score - 20


def test_soc_in_a_hardware_title_is_a_system_on_chip():
    verdict = score_posting(
        title="Junior Hardware Engineer (SOC - FPGA and Firmware)", company="GMV"
    )
    assert "Fuori dal tuo profilo" in _flags(verdict)


def test_an_advert_written_in_french_flags_the_language():
    advert = (
        "Vous rejoindrez une équipe pour la sécurité des systèmes. Les missions sont "
        "variées, avec des projets pour les clients et des échanges dans une équipe "
        "internationale. Nous recherchons une personne pour des analyses avec les équipes "
        "dans le domaine des systèmes pour une entreprise."
    )
    verdict = score_posting(title="Ingénieur Cybersécurité", company="Thales", description=advert)
    assert "Lingua: francese" in _flags(verdict)


def test_a_language_asked_for_by_name_is_flagged():
    verdict = score_posting(
        title="Security Analyst",
        company="secunet",
        description="You speak fluent German and English.",
    )
    assert "Lingua: tedesco" in _flags(verdict)


def test_a_passed_deadline_halves_the_score():
    open_ = score_posting(title="Security Analyst", company="ENISA", deadline="2026-12-01",
                          today=date(2026, 10, 3))
    passed = score_posting(title="Security Analyst", company="ENISA", deadline="2026-09-01",
                           today=date(2026, 10, 3))
    assert passed.score == pytest.approx(open_.score / 2, abs=1)
    assert "Scadenza passata" in _flags(passed)


def test_case_does_not_matter():
    assert (
        score_posting(title="CYBER DEFENCE ENGINEER", company="AIRBUS").score
        == score_posting(title="cyber defence engineer", company="Airbus").score
    )


# ---------------------------------------------------------------------------
# The prefilter that keeps ranking the table to seconds
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("alternative", "literal"),
    [
        (r"\bu\.?s\.? citizen", "citizen"),
        (r"must be (a )?u\.?s\.?", "must be"),
        (r"account (executive|manager|director|exec)", "account"),
        (r"\b(sc|dv)\b (level )?clearance", "clearance"),
        (r"\bph\.?d\b", "ph"),
        (r"post-?doc", "post"),
        (r"iris\s?²", "iris"),
    ],
)
def test_the_literal_is_one_every_match_contains(alternative, literal):
    assert _literal(alternative) == literal


def test_a_leading_word_boundary_is_still_honoured():
    terms = _Terms(r"\bcti\b", r"\bai\b")
    assert terms.search("a cti team")
    assert terms.search("(ai)")
    assert not terms.search("practical")
    assert not terms.search("maintain")


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------
def test_refresh_ranks_and_the_priority_sort_reads_it():
    _insert("a", "Account Executive", "Airbus")
    _insert("b", "Satellite Cybersecurity Architect", "Aerospacelab", location="Belgium")
    _insert("c", "Cyber Threat Intelligence Analyst", "Tiny Cyber")

    assert ranking.refresh() == 3
    assert ranking.refresh() == 0  # nothing changed

    jobs = db.list_jobs(sort="priority", excluded="include").items
    assert [job.id for job in jobs] == ["b", "c", "a"]
    assert jobs[0].priority_source == "rules"
    assert jobs[0].priority_criteria[0] == {"name": "Organizzazione", "points": 25, "max": 25}
    # The sales role is ranked, and left out of the search unless asked for.
    assert jobs[2].priority_excluded == ranking.EXCLUDED_SUPPORT
    assert [job.id for job in db.list_jobs(sort="priority").items] == ["b", "c"]
    assert [job.id for job in db.list_jobs(excluded="only").items] == ["a"]


def test_a_changed_posting_is_ranked_again():
    _insert("a", "Security Analyst", "ENISA")
    ranking.refresh()
    with db.connect() as conn:
        conn.execute(
            "UPDATE jobs SET title = 'Senior Security Analyst', "
            "updated_at = datetime('now', '+1 second') WHERE id = 'a'"
        )
    assert ranking.refresh() == 1
    assert db.get_job("a").priority_flags[0]["text"] == "Senior"


def test_newer_rules_rank_everything_again(monkeypatch):
    _insert("a", "Security Analyst", "ENISA")
    ranking.refresh()
    monkeypatch.setattr(ranking, "RULES_VERSION", "next")
    assert ranking.refresh() == 1


def test_a_page_load_does_not_wait_for_a_ranking_in_progress():
    _insert("a", "Security Analyst", "ENISA")
    with ranking._refresh_lock:
        ranking.refresh_quietly()  # returns at once instead of deadlocking
    assert db.get_job("a").priority is None


# ---------------------------------------------------------------------------
# The review
# ---------------------------------------------------------------------------
def _review_payload(**fields) -> dict:
    verdict = {
        "criteria": [
            {"name": "Feasibility", "points": 40, "max": 40},
            {"name": "CV fit", "points": 18, "max": 20},
            {"name": "Organisation weight", "points": 20, "max": 20},
            {"name": "Sector and mission", "points": 20, "max": 20},
        ],
        "adjustments": [{"name": "Location", "points": 5}, {"name": "Salary", "points": -2}],
        "total": 101,
        "cap": 0,
        "eligible": True,
        "justification": "Molto allineata a IRIS².",
        "issues": ["Verificare la clearance"],
        "cv_tip": "Lo stage ESA.",
        "deadline": "missing data",
        "location": "Betzdorf",
        "languages": "English",
        "clearance": "SECRET",
        **fields,
    }
    return {"choices": [{"message": {"content": json.dumps(verdict)}}], "usage": {"cost": 0.002}}


class FakeClient:
    def __init__(self, **fields) -> None:
        self.calls: list[dict] = []
        self.fields = fields

    def complete_raw(self, messages, **overrides):
        self.calls.append({"messages": messages, **overrides})
        return _review_payload(**self.fields)

    def close(self) -> None:
        pass


def _brief(text: str = "Sono un ingegnere della sicurezza.") -> None:
    paths.RANKING_BRIEF_PATH.write_text(text, encoding="utf-8")


def test_a_review_needs_a_brief():
    with pytest.raises(ranking.BriefMissing):
        ranking.review_top(5, client=FakeClient(), say=lambda _: None)


def test_the_review_replaces_the_rules_score_and_is_not_repeated():
    _brief()
    _insert("a", "Information Security Analyst, IRIS2", "SES", description="IRIS² security.")
    _insert("b", "Security Analyst", "Tiny Cyber")
    low = [{"name": "Feasibility", "points": 5, "max": 40}, {"name": "CV fit", "points": 5, "max": 20}]
    client = FakeClient(criteria=low, adjustments=[])

    run = ranking.review_top(1, client=client, say=lambda _: None)

    assert run.reviewed == 1 and run.cost == pytest.approx(0.002)
    sent = client.calls[0]
    assert "Sono un ingegnere della sicurezza." in sent["messages"][0]["content"]
    assert "IRIS² security." in sent["messages"][1]["content"]
    assert sent["response_format"]["json_schema"]["schema"]["required"] == list(
        ranking.REVIEW_PROPERTIES
    )
    job = db.get_job("a")
    assert (job.priority, job.priority_source) == (10, "review")
    assert job.priority_summary == "Molto allineata a IRIS²."
    assert job.priority_issues == ["Verificare la clearance"]
    assert job.priority_facts["Location"] == "Betzdorf"
    assert not job.priority_stale
    # The review moved it below a posting the rules put lower.
    assert [j.id for j in db.list_jobs(sort="priority").items][0] == "b"

    assert ranking.review_top(1, mode="new", client=client, say=lambda _: None).reviewed == 1
    assert ranking.review_top(5, client=client, say=lambda _: None).reviewed == 0
    _brief("Un altro brief.")
    assert db.get_job("a").priority_stale
    assert ranking.review_top(5, mode="new", client=client, say=lambda _: None).reviewed == 0
    assert ranking.review_top(5, client=client, say=lambda _: None).reviewed == 2


def test_a_uk_role_tells_the_model_the_visa_does_not_count():
    row = {
        "company": "Jane Street", "title": "Cybersecurity Engineer", "seniority": "", "date_posted": None,
        "deadline": None, "apply_url": "https://x", "description": "Secure the firm.",
    }
    assert ranking.review_prompt({**row, "location": "London, United Kingdom"}).startswith(ranking.UK_VISA_NOTE)
    assert ranking.UK_VISA_NOTE not in ranking.review_prompt({**row, "location": "Paris, France"})


def test_a_long_notice_is_sent_whole():
    advert = "Duties. " * 2000 + "Eligibility: EU citizenship and 3 years of experience."
    prompt = ranking.review_prompt(
        {
            "company": "EDA",
            "title": "Project Officer",
            "location": "Brussels",
            "seniority": "",
            "date_posted": None,
            "deadline": None,
            "apply_url": "https://x",
            "description": advert,
        }
    )
    assert len(advert) > 9000 and prompt.endswith("3 years of experience.")


def test_the_score_is_the_sum_of_criteria_and_adjustments():
    _brief()
    _insert("a", "Security Analyst", "ENISA")
    ranking.review_top(1, client=FakeClient(total=7), say=lambda _: None)
    job = db.get_job("a")
    # 40 + 18 + 20 + 20, +5 -2: the model's own addition (7) is not trusted.
    assert job.priority == 101
    assert job.priority_adjustments == [{"name": "Location", "points": 5}, {"name": "Salary", "points": -2}]
    assert [c["name"] for c in job.priority_criteria][0] == "Feasibility"


def test_the_brief_cap_holds_the_score_and_shows_on_the_card():
    _brief()
    _insert("a", "Security Trainee", "ENISA")
    ranking.review_top(1, client=FakeClient(cap=40), say=lambda _: None)
    job = db.get_job("a")
    assert job.priority == 40
    # The card adds up: criteria 98, +3 of adjustments, then the cap.
    assert job.priority_adjustments[-1] == {"name": "Tetto 40", "points": -61}

    _insert("b", "Security Analyst", "ENISA")
    ranking.review_top(1, client=FakeClient(cap=0), say=lambda _: None)
    assert db.get_job("b").priority == 101  # no cap for it in the brief


def test_excluded_postings_are_never_sent_for_review():
    _brief()
    _insert("a", "Security Analyst", "Thales", location="Austin, TX")
    _insert("b", "Senior Security Architect", "ENISA")
    client = FakeClient()
    assert ranking.review_top(None, client=client, say=lambda _: None).reviewed == 0
    assert client.calls == []


def test_a_review_that_rules_the_posting_out_says_so():
    _brief()
    _insert("a", "Security Analyst", "ENISA")
    ranking.review_top(1, client=FakeClient(eligible=False), say=lambda _: None)
    job = db.get_job("a")
    assert job.priority_flags[0] == {
        "text": "Non candidabile secondo la revisione",
        "level": "block",
    }
    # It sinks below anything that can be applied for, whatever its merits.
    assert job.priority == ranking.NOT_ELIGIBLE_CAP


def test_out_of_range_review_figures_are_held_to_their_maximum():
    review = ranking.parse_review(
        {
            "criteria": [{"name": "CV fit", "points": 35, "max": 20}, {"name": "", "points": 9, "max": 9}],
            "adjustments": [{"name": "Salary", "points": 400}],
            "issues": None,
        }
    )
    assert review["criteria"] == [{"name": "CV fit", "points": 20, "max": 20}]
    assert review["adjustments"] == [{"name": "Salary", "points": 100}]
    assert review["score"] == 120
    assert review["issues"] == []


def test_a_review_stored_before_the_brief_named_its_criteria_still_reads():
    _insert("a", "Security Analyst", "ENISA")
    old = {
        "punteggio": 80,
        "criteria": {"organizzazione": 25, "cv": 15},
        "candidabile": True,
        "motivo": "Buona.",
        "problemi": [],
        "enfasi_cv": "",
        "brief": "old",
    }
    with db.connect() as conn:
        conn.execute(
            "UPDATE jobs SET priority_llm_score = 80, priority_llm = ? WHERE id = 'a'",
            (json.dumps(old),),
        )
    job = db.get_job("a")
    assert (job.priority, job.priority_summary) == (80, "Buona.")
    assert job.priority_criteria[1] == {"name": "Aderenza al CV", "points": 15, "max": 20}


def test_the_api_sorts_by_priority(client):
    _insert("a", "Security Engineer", "Tiny Cyber")
    _insert("b", "Cyber Threat Intelligence Analyst", "ENISA")
    response = client.get("/api/jobs", params={"sort": "priority"})
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == ["b", "a"]


class DownClient(FakeClient):
    def complete_raw(self, messages, **overrides):
        self.calls.append({})
        raise ranking_llm_error("OpenRouter request failed: Temporary failure in name resolution")


def ranking_llm_error(message: str) -> Exception:
    from acide.llm import InferenceError

    return InferenceError(message)


class FlakyClient(FakeClient):
    """Drops the first call for each posting, as a provider timing out does."""

    def complete_raw(self, messages, **overrides):
        self.calls.append({})
        if len(self.calls) % 2:
            raise ranking_llm_error("OpenRouter request failed: The read operation timed out")
        return _review_payload()


def test_a_failed_call_is_tried_once_more():
    _brief()
    _insert("a", "Security Analyst", "ENISA")
    client = FlakyClient()
    run = ranking.review_top(1, client=client, say=lambda _: None)
    assert (run.reviewed, run.failed, len(client.calls)) == (1, 0, 2)


def test_a_run_gives_up_when_the_gateway_is_down(monkeypatch):
    # A DNS outage once failed 312 reviews in a row.
    _brief()
    for n in range(30):
        _insert(f"j{n}", f"Security Analyst {n}", "ENISA")
    monkeypatch.setattr(ranking, "GIVE_UP_AFTER", 3)
    client = DownClient()

    run = ranking.review_top(30, client=client, say=lambda _: None)

    assert run.stopped.startswith("3 failures in a row")
    assert run.reviewed == 0
    # Each posting is tried twice; stopping means far fewer than all of them.
    assert len(client.calls) < 2 * 30
    # Nothing was stored, so the next run picks all of them up.
    assert len(ranking.candidates_for_review(30, ranking.brief_digest("x"))) == 30

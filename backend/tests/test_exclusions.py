"""The operator's own filters: words a posting must not have."""

from __future__ import annotations

from test_ranking import FakeClient, _brief, _insert

from acide import db, exclusions, ranking
from acide.exclusions import Filters, Matcher


def _reason(posting: str | tuple[str, str, str], **filters: list[str]) -> str:
    """Why `filters` exclude the posting: a title, or (title, company, location)."""
    title, company, location = (posting, "", "") if isinstance(posting, str) else posting
    return Matcher(Filters(**filters)).reason(title=title, company=company, location=location)


def test_a_word_matches_whole_words_ignoring_case_and_accents():
    assert _reason("Stage Ingénieur Cybersécurité", title=["stage"]) == "Filtro titolo: stage"
    assert _reason("Backstage Security Engineer", title=["stage"]) == ""
    assert _reason("Ingénieur sécurité", title=["ingenieur"]) == "Filtro titolo: ingenieur"
    assert _reason("Sr. Security Engineer", title=["Sr."]) == "Filtro titolo: Sr."


def test_a_star_matches_the_start_of_a_word_and_a_phrase_spans_hyphens():
    assert _reason("Security Internship", title=["intern*"]) == "Filtro titolo: intern*"
    assert _reason("Security Intern", title=["intern"]) == "Filtro titolo: intern"
    assert _reason("Security Internship", title=["intern"]) == ""
    assert _reason("Red-Team Operator", title=["red team"]) == "Filtro titolo: red team"


def test_each_field_is_read_on_its_own():
    posting = ("Security Engineer", "Acme Corp", "Austin, TX")
    assert _reason(posting, company=["acme"]) == "Filtro azienda: acme"
    assert _reason(posting, location=["tx"]) == "Filtro sede: tx"
    assert _reason(("Acme Security Engineer", "Other", ""), company=["acme"]) == ""


def test_saving_cleans_the_lists(isolated_state):
    saved = exclusions.save({"title": ["  Senior ", "senior", "", "*", "Stage  tecnico"], "company": "A\nB"})
    assert saved.title == ["Senior", "Stage tecnico"]
    assert saved.company == ["A", "B"]
    assert exclusions.load() == saved


def test_filters_exclude_postings_and_removing_them_restores_the_rules_reason():
    _insert("a", "Senior Security Architect", "ENISA")
    _insert("b", "Security Analyst Stage", "ENISA")
    _insert("c", "Security Analyst", "ENISA")
    ranking.refresh()

    def excluded() -> dict[str, str]:
        with db.connect() as conn:
            return {r["id"]: r["priority_excluded"] for r in conn.execute("SELECT id, priority_excluded FROM jobs")}

    assert excluded() == {"a": ranking.EXCLUDED_SENIOR, "b": "", "c": ""}

    exclusions.save({"title": ["stage", "senior"]})
    assert ranking.refresh() == 0  # nothing ranked again: the filters alone are applied
    assert excluded() == {"a": "Filtro titolo: senior", "b": "Filtro titolo: stage", "c": ""}

    exclusions.save({})
    ranking.refresh()
    assert excluded() == {"a": ranking.EXCLUDED_SENIOR, "b": "", "c": ""}


def test_a_filtered_posting_is_never_reviewed(isolated_state):
    _brief()
    _insert("a", "Security Analyst Stage", "ENISA")
    exclusions.save({"title": ["stage"]})
    client = FakeClient()
    assert ranking.review_top(None, client=client, say=lambda _: None).reviewed == 0
    assert client.calls == []


def test_the_api_saves_and_reports_the_filters(client):
    _insert("a", "Security Analyst Stage", "ENISA")
    response = client.put("/api/search/filters", json={"title": ["stage"], "company": [], "location": []})
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == ["stage"]
    assert body["by_filter"] == {"Filtro titolo: stage": 1}
    assert client.get("/api/search/filters").json()["excluded"] == 1

"""Countries read from the locations the sites write, and the "Where" filter."""

from __future__ import annotations

import pytest

from acide import db, ranking
from acide.geo import countries


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ("Madrid, MD, ES", ["ES"]),  # MD is the Comunidad de Madrid, not Moldova
        ("IT - Roma - Via Laurentina, Italy", ["IT"]),
        ("IT - Torino - C.so Francia, Italy", ["IT"]),  # a street, not France
        ("DE - Darmstadt - Europahaus, Germany", ["DE"]),
        ("Darmstadt, DE", ["DE"]),  # Germany, not Delaware
        ("Office - USA - CA - Headquarters, United States of America", ["US"]),
        ("King of Prussia, PA", ["US"]),
        ("San Diego, CA", ["US"]),
        ("Burnaby, BC, Canada", ["CA"]),
        ("Herzliya, Tel Aviv, Israel, IL", ["IL"]),  # Israel, not Illinois
        ("Woolloomooloo, New South Wales, Australia", ["AU"]),  # not Wales
        ("Türkiye-Izmir", ["TR"]),
        ("United States-Illinois-Rolling Meadows", ["US"]),
        ("Berlin, DEU, 10969", ["DE"]),
        ("Betzdorf, LU", ["LU"]),
        ("Grenoble", ["FR"]),
        ("Remote; UK, France, Netherlands", ["GB", "FR", "NL"]),
        ("Not specified", []),
    ],
)
def test_the_countries_a_location_names(location, expected):
    assert countries(location) == expected


def _insert(job_id: str, company: str, location: str) -> None:
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO jobs (id, external_id, source_type, title, company, location, apply_url)
               VALUES (?, ?, 'extract', 'Security Analyst', ?, ?, ?)""",
            (job_id, job_id, company, location, f"https://x/{job_id}"),
        )


def test_the_search_filters_by_country_and_by_europe(client):
    _insert("rome", "Leonardo", "Roma, Italy")
    _insert("oslo", "Kongsberg", "Oslo, Norway")
    _insert("lux", "SES", "Betzdorf, LU")
    _insert("nato", "NATO", "Norfolk, Virginia, United States")  # no visa: NATO
    ranking.refresh()

    def ids(**params):
        return sorted(item["id"] for item in client.get("/api/jobs", params=params).json()["items"])

    assert ids(country="IT") == ["rome"]
    assert ids(country="EUROPE") == ["lux", "oslo", "rome"]
    assert ids() == ["lux", "nato", "oslo", "rome"]


def test_a_location_without_a_country_takes_the_employers_usual_one():
    for n in range(4):
        _insert(f"cea{n}", "CEA", "Saclay, Essonne, France")
    _insert("bare", "CEA", "Bât. 451, centre d'études")
    ranking.refresh()
    assert db.list_jobs(country="FR", excluded="include").total == 5


def test_the_facets_list_europe_first_then_countries_by_postings(client):
    _insert("a", "Leonardo", "Roma, Italy")
    _insert("b", "Leonardo", "Milano, Italy")
    _insert("c", "Thales", "Paris, France")
    ranking.refresh()
    facets = client.get("/api/jobs/facets").json()["countries"]
    assert facets[0] == {"code": "EUROPE", "name": "Europe (all countries)", "count": 3}
    assert facets[1:] == [
        {"code": "IT", "name": "Italy", "count": 2},
        {"code": "FR", "name": "France", "count": 1},
    ]

"""The filter matrix: every control must actually narrow the result set."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from acide import db
from acide.models import JobEvaluation, RawPosting


def _today(offset_days: int = 0) -> str:
    return (datetime.now(UTC) - timedelta(days=offset_days)).date().isoformat()


def seed(**overrides):
    """Insert one scored posting, overriding any field."""
    posting_fields = {
        "external_id": overrides.pop("external_id", "1"),
        "company": overrides.pop("company", "Acme"),
        "title": overrides.pop("title", "Software Engineer"),
        "location": overrides.pop("location", "Cupertino, California, United States"),
        "apply_url": overrides.pop("apply_url", "https://example.com/1"),
        "description": overrides.pop("description", ""),
        "date_posted": overrides.pop("date_posted", _today()),
        "source_type": overrides.pop("source_type", "greenhouse"),
        "currency": overrides.pop("posting_currency", None),
        "amount": overrides.pop("posting_amount", None),
        "rate": overrides.pop("posting_rate", None),
    }
    evaluation_fields = {
        "seniority": "Mid-Level",
        "category": "Embedded / Systems",
        "years_experience_min": 4,
        "rate": "Yearly",
        "currency": "USD",
        "amount": 0.0,
        "experience_fit_score": 80,
        "interest_fit_score": 40,
        "category_type": "Direct Match",
        "transferable_skills": [],
        "skills_to_learn": [],
        "alert_summary": "",
    }
    evaluation_fields.update(overrides)
    return db.upsert_job(RawPosting(**posting_fields), JobEvaluation(**evaluation_fields))


def titles(**filters) -> set[str]:
    return {job.title for job in db.list_jobs(**filters).items}


def test_search_spans_title_company_and_category():
    seed(external_id="1", title="Embedded Engineer", company="Acme")
    seed(external_id="2", title="Data Scientist", company="Globex", category="Data")
    assert titles(search="Embedded") == {"Embedded Engineer"}
    assert titles(search="Globex") == {"Data Scientist"}
    assert titles(search="Data") == {"Data Scientist"}


def test_location_and_remote_filters_differ():
    seed(external_id="1", title="Onsite Role", location="Rome, Italy")
    seed(external_id="2", title="Remote Role", location="Remote - Italy")
    assert titles(location="Italy") == {"Onsite Role", "Remote Role"}
    # "Remote from ..." must not match an onsite posting in the same country.
    assert titles(remote="Italy") == {"Remote Role"}


def test_category_seniority_and_company_filters():
    seed(external_id="1", title="A", category="Cloud Security", seniority="Senior", company="Acme")
    seed(external_id="2", title="B", category="Embedded / Systems", seniority="Junior", company="Globex")
    assert titles(category="Cloud Security") == {"A"}
    assert titles(seniority="Junior") == {"B"}
    assert titles(company="glob") == {"B"}  # case-insensitive substring


def test_years_of_experience_buckets():
    seed(external_id="1", title="Junior", years_experience_min=1)
    seed(external_id="2", title="Mid", years_experience_min=4)
    seed(external_id="3", title="Veteran", years_experience_min=12)
    assert titles(years_experience="0-2") == {"Junior"}
    assert titles(years_experience="3-5") == {"Mid"}
    assert titles(years_experience="10+") == {"Veteran"}


def test_date_posted_window():
    seed(external_id="1", title="Fresh", date_posted=_today(0))
    seed(external_id="2", title="Stale", date_posted=_today(45))
    assert titles(posted_within="week") == {"Fresh"}
    assert titles(posted_within="month") == {"Fresh"}
    assert titles() == {"Fresh", "Stale"}


def test_amount_filter_normalises_rate_and_currency():
    seed(external_id="1", title="Hourly USD", posting_amount=100, posting_rate="Hourly",
         posting_currency="USD")                       # ≈ 208k USD/yr
    seed(external_id="2", title="Yearly EUR", posting_amount=100_000, posting_rate="Yearly",
         posting_currency="EUR")                       # ≈ 109k USD/yr
    seed(external_id="3", title="Monthly GBP", posting_amount=5_000, posting_rate="Monthly",
         posting_currency="GBP")                       # ≈ 76k USD/yr

    result = titles(min_amount=150_000, rate="Yearly", currency="USD")
    assert result == {"Hourly USD"}

    result = titles(min_amount=100_000, rate="Yearly", currency="USD")
    assert result == {"Hourly USD", "Yearly EUR"}


def test_match_posting_currency_removes_conversion():
    seed(external_id="1", title="EUR role", posting_amount=100_000, posting_rate="Yearly",
         posting_currency="EUR")
    seed(external_id="2", title="USD role", posting_amount=100_000, posting_rate="Yearly",
         posting_currency="USD")
    # With the box ticked only same-currency postings are eligible.
    assert titles(min_amount=90_000, rate="Yearly", currency="EUR",
                  match_posting_currency=True) == {"EUR role"}
    assert titles(min_amount=90_000, rate="Yearly", currency="USD",
                  match_posting_currency=True) == {"USD role"}


def test_postings_without_published_pay_survive_the_amount_filter():
    # Most ATS feeds omit salary; hiding those roles would empty the grid.
    seed(external_id="1", title="Undisclosed", amount=0.0)
    assert titles(min_amount=200_000, rate="Yearly", currency="USD") == {"Undisclosed"}


def test_dismissed_jobs_are_hidden_but_recoverable():
    job_id = seed(external_id="1", title="Nope")
    db.set_job_flag(job_id, "dismissed", True)
    assert titles() == set()
    assert titles(include_dismissed=True) == {"Nope"}


def test_saved_only_returns_bookmarks():
    saved_id = seed(external_id="1", title="Saved")
    seed(external_id="2", title="Unsaved")
    db.set_job_flag(saved_id, "saved", None)  # toggle
    assert titles(saved_only=True) == {"Saved"}


def test_sorting_options():
    seed(external_id="1", title="HighExp", experience_fit_score=95, interest_fit_score=10)
    seed(external_id="2", title="HighInterest", experience_fit_score=20, interest_fit_score=99)
    assert db.list_jobs(sort="experience").items[0].title == "HighExp"
    assert db.list_jobs(sort="interest").items[0].title == "HighInterest"
    # Default "fit" ranks on whichever vector is strongest.
    assert db.list_jobs(sort="fit").items[0].title == "HighInterest"


def test_pagination_reports_remaining_pages():
    for index in range(5):
        seed(external_id=str(index), title=f"Role {index}")
    page = db.list_jobs(limit=2, offset=0)
    assert page.total == 5 and len(page.items) == 2 and page.has_more
    last = db.list_jobs(limit=2, offset=4)
    assert not last.has_more


def test_rescoring_a_posting_preserves_the_bookmark():
    job_id = seed(external_id="1", title="Original", experience_fit_score=50)
    db.set_job_flag(job_id, "saved", True)
    seed(external_id="1", title="Retitled", experience_fit_score=90)
    job = db.get_job(job_id)
    assert job.title == "Retitled"
    assert job.experience_fit_score == 90
    assert job.saved is True  # user state is never clobbered by a re-index


def test_facets_list_only_visible_values():
    seed(external_id="1", category="Cloud Security", company="Acme")
    hidden = seed(external_id="2", category="Hidden Category", company="Ghost")
    db.set_job_flag(hidden, "dismissed", True)
    facets = db.facets()
    assert "Cloud Security" in facets.categories
    assert "Hidden Category" not in facets.categories
    assert facets.companies == ["Acme"]

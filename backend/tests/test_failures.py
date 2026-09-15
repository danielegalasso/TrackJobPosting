"""Grouping a run's failures by cause.

Every message here is taken verbatim from the overnight log that motivated
the grouping: 2,798 errors, of which 2,783 were one rejected response schema
and one was a careers URL that had rotted — a fact the old summary, which
printed the first fifteen, could not show.
"""

from __future__ import annotations

from acide import failures

#: The exact body OpenRouter returned for every scoring call that night, with
#: the truncation the runner records.
_SCHEMA_400 = (
    'OpenRouter returned HTTP 400: {"error":{"message":"Provider returned error",'
    '"code":400,"metadata":{"raw":"{\\n  \\"error\\": {\\n    \\"message\\": '
    '\\"Invalid schema for response_format \'job_evaluation\': In context=(), '
    "'required' is required to be supplied and to be an array including every "
    'key in properties. Missing \'rate\'.\\",\\n    \\"'
)

_TITLES = (
    "Penetration Tester (All Gender)",
    "Ingénieur Support Produits Cyber (h/f)",
    "IM Solution Architect (M/F)",
    "Junior Backend Engineer (m/w/d)",
    "Software Requirements and Test Engineer",
    "Legal Counsel (w, m, d)",
)


def _overnight_errors() -> list[str]:
    errors = [f"{title}: {_SCHEMA_400}" for title in _TITLES]
    errors.append("TU Munchen: https://www.tum.de/en/about-tum/working-at-tum: HTTP 404")
    return errors


def test_one_fault_across_thousands_of_postings_is_one_cause():
    causes = failures.group(_overnight_errors())

    assert len(causes) == 2, "one schema rejection and one dead URL"
    assert causes[0].count == len(_TITLES)
    assert causes[0].subjects[:2] == list(_TITLES[:2])
    assert "Missing 'rate'" in causes[0].detail


def test_the_single_rotted_url_survives_the_flood():
    """The point of the change: it was invisible behind the first fifteen lines."""
    flood = [f"Posting {index}: {_SCHEMA_400}" for index in range(2783)]
    causes = failures.group(flood + _overnight_errors())

    assert [cause.count for cause in causes] == [2783 + len(_TITLES), 1]
    dead_url = causes[-1]
    assert dead_url.subjects == ["TU Munchen"]
    assert "HTTP 404" in dead_url.detail

    printed = "\n".join(failures.render(causes))
    assert "TU Munchen" in printed
    assert "www.tum.de" in printed


def test_the_same_failure_at_different_urls_groups_together():
    causes = failures.group(
        [
            "TU Munchen: https://www.tum.de/en/about-tum/working-at-tum: HTTP 404",
            "Politecnico: https://www.polimi.it/lavora-con-noi: HTTP 404",
        ]
    )
    assert len(causes) == 1
    assert causes[0].subjects == ["TU Munchen", "Politecnico"]


def test_a_refusal_and_a_dead_page_stay_apart():
    """403 and 404 are different diagnoses; a fifth of the list once turned on it."""
    causes = failures.group(
        [
            "TU Munchen: https://www.tum.de/en/about-tum/working-at-tum: HTTP 404",
            "Cloudflare: https://www.cloudflare.com/careers/: HTTP 403",
        ]
    )
    assert len(causes) == 2


def test_a_message_with_no_subject_still_counts():
    causes = failures.group(["no enabled targets configured"])
    assert causes[0].count == 1
    assert causes[0].subjects == []
    assert causes[0].detail == "no enabled targets configured"


def test_render_names_the_ones_it_could_not_list():
    causes = failures.group([f"Company {index}: HTTP 500" for index in range(10)])
    printed = "\n".join(failures.render(causes, subjects=3))
    assert "Company 0, Company 1, Company 2, and 7 more" in printed

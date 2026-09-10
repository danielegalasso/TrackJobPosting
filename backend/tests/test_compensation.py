"""Compensation normalisation: the maths behind the rate/currency/amount row."""

from __future__ import annotations

import pytest

from acide.compensation import (
    annualised_usd,
    convert,
    normalise_amount,
    parse_compensation,
)


def test_annualises_each_rate():
    assert annualised_usd(100, "Hourly", "USD") == pytest.approx(208_000)
    assert annualised_usd(1000, "Daily", "USD") == pytest.approx(260_000)
    assert annualised_usd(10_000, "Monthly", "USD") == pytest.approx(120_000)
    assert annualised_usd(150_000, "Yearly", "USD") == pytest.approx(150_000)


def test_converts_across_rate_and_currency():
    # 120k EUR/year restated as monthly USD.
    monthly_usd = convert(120_000, "Yearly", "EUR", "Monthly", "USD")
    assert monthly_usd == pytest.approx(120_000 * 1.09 / 12)


def test_unknown_currency_falls_back_to_parity():
    assert annualised_usd(1000, "Yearly", "XYZ") == pytest.approx(1000)


def test_threshold_sql_omits_fx_when_matching_posting_currency():
    sql, params = normalise_amount(150_000, "Yearly", "EUR", match_posting_currency=True)
    assert "currency" not in sql  # only the rate CASE, no FX conversion
    assert params == [150_000]


def test_threshold_sql_includes_fx_when_converting():
    sql, params = normalise_amount(150_000, "Yearly", "EUR", match_posting_currency=False)
    assert "CASE currency" in sql
    assert params[0] == pytest.approx(150_000 * 1.09)


@pytest.mark.parametrize(
    ("text", "amount", "currency", "rate"),
    [
        ("Base pay range: $165,000 - $210,000 per year.", 165_000, "USD", "Yearly"),
        ("Salary EUR 85,000 per year.", 85_000, "EUR", "Yearly"),
        ("£95,000 annually", 95_000, "GBP", "Yearly"),
        ("$85 per hour, contract", 85, "USD", "Hourly"),
        ("Compensation: 140k USD", 140_000, "USD", "Yearly"),
    ],
)
def test_parses_published_compensation(text, amount, currency, rate):
    parsed_amount, parsed_currency, parsed_rate = parse_compensation(text)
    assert parsed_amount == pytest.approx(amount)
    assert parsed_currency == currency
    assert parsed_rate == rate


def test_returns_nothing_when_pay_is_not_published():
    amount, currency, rate = parse_compensation(
        "We offer a competitive salary and equity package."
    )
    assert amount is None
    assert currency is None


def test_range_reports_the_lower_bound():
    # The Amount filter reads as a floor, so a range must not be represented
    # by its top end or every range would clear every threshold.
    amount, _, _ = parse_compensation("$150,000 — $250,000 per year")
    assert amount == pytest.approx(150_000)

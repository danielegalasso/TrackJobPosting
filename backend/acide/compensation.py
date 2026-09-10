"""Compensation normalisation for the rate / currency / amount filter row.

Postings arrive quoted in whatever unit the employer used -- an hourly USD
contract rate next to a yearly EUR salary -- so the `Amount` filter has to
compare them on one axis. Everything is annualised and expressed in USD
before comparison.

The FX table is a static, deliberately coarse snapshot: it exists to make
"at least ~150k" behave sensibly across currencies, not to price anything.
Operators who need accuracy should tick `Match posting currency`, which
removes conversion from the comparison entirely.
"""

from __future__ import annotations

import re
from typing import Any

# Periods per year, used to annualise a quoted rate.
RATE_PERIODS: dict[str, float] = {
    "Hourly": 2080.0,   # 40h x 52 weeks
    "Daily": 260.0,     # 5d x 52 weeks
    "Monthly": 12.0,
    "Yearly": 1.0,
}

# Units of USD per 1 unit of the quoted currency. Coarse by design.
USD_PER_UNIT: dict[str, float] = {
    "USD": 1.0,
    "EUR": 1.09,
    "GBP": 1.27,
    "CHF": 1.12,
    "CAD": 0.74,
    "AUD": 0.66,
    "SEK": 0.095,
    "NOK": 0.093,
    "DKK": 0.146,
    "PLN": 0.25,
    "INR": 0.012,
    "JPY": 0.0067,
    "SGD": 0.74,
    "ILS": 0.27,
    "BRL": 0.18,
    "MXN": 0.055,
}

SUPPORTED_CURRENCIES: tuple[str, ...] = tuple(USD_PER_UNIT)

_CURRENCY_SYMBOLS: dict[str, str] = {
    "$": "USD",
    "US$": "USD",
    "€": "EUR",
    "£": "GBP",
    "₹": "INR",
    "¥": "JPY",
    "CHF": "CHF",
    "kr": "SEK",
}


def rate_periods(rate: str | None) -> float:
    return RATE_PERIODS.get((rate or "Yearly").title(), 1.0)


def usd_per_unit(currency: str | None) -> float:
    return USD_PER_UNIT.get((currency or "USD").upper(), 1.0)


def annualised_usd(amount: float, rate: str | None, currency: str | None) -> float:
    """Express a quoted figure as USD per year."""
    return float(amount) * rate_periods(rate) * usd_per_unit(currency)


def convert(
    amount: float,
    from_rate: str | None,
    from_currency: str | None,
    to_rate: str | None,
    to_currency: str | None,
) -> float:
    """Restate a quoted figure in a different rate and currency."""
    annual_usd = annualised_usd(amount, from_rate, from_currency)
    return annual_usd / usd_per_unit(to_currency) / rate_periods(to_rate)


def _rate_case(column: str = "rate") -> str:
    """SQL CASE mapping a stored rate onto its periods-per-year factor."""
    branches = " ".join(
        f"WHEN '{name}' THEN {periods}" for name, periods in RATE_PERIODS.items()
    )
    return f"(CASE {column} {branches} ELSE 1.0 END)"


def _fx_case(column: str = "currency") -> str:
    """SQL CASE mapping a stored currency onto USD per unit."""
    branches = " ".join(
        f"WHEN '{code}' THEN {factor}" for code, factor in USD_PER_UNIT.items()
    )
    return f"(CASE {column} {branches} ELSE 1.0 END)"


def normalise_amount(
    min_amount: float,
    target_rate: str,
    target_currency: str,
    match_posting_currency: bool,
) -> tuple[str, list[Any]]:
    """Build the `amount >= threshold` predicate for `build_job_query`.

    When `match_posting_currency` is set the caller has already pinned
    `currency = ?`, so only the rate needs converting and no exchange rate
    enters the comparison.
    """
    target_periods = rate_periods(target_rate)
    if match_posting_currency:
        threshold = float(min_amount) * target_periods
        return f"amount * {_rate_case()} >= ?", [threshold]

    threshold_usd = float(min_amount) * target_periods * usd_per_unit(target_currency)
    return f"amount * {_rate_case()} * {_fx_case()} >= ?", [threshold_usd]


_AMOUNT_RE = re.compile(
    r"(?P<symbol>US\$|[$€£₹¥]|CHF|\b[A-Z]{3}\b)?\s*"
    r"(?P<value>\d{1,3}(?:[,\s]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(?P<suffix>[kK]\b)?"
)

_PERIOD_HINTS: tuple[tuple[str, str], ...] = (
    ("per hour", "Hourly"),
    ("/hour", "Hourly"),
    ("/hr", "Hourly"),
    ("hourly", "Hourly"),
    ("per day", "Daily"),
    ("/day", "Daily"),
    ("daily", "Daily"),
    ("per month", "Monthly"),
    ("/month", "Monthly"),
    ("monthly", "Monthly"),
    ("per year", "Yearly"),
    ("/year", "Yearly"),
    ("annually", "Yearly"),
    ("annual", "Yearly"),
    ("yearly", "Yearly"),
)


def parse_compensation(text: str) -> tuple[float | None, str | None, str | None]:
    """Best-effort extraction of (amount, currency, rate) from free text.

    Used for ATS feeds that publish pay only as prose. Ambiguity resolves to
    `None`, which the portal renders as "no published compensation" rather
    than inventing a number.
    """
    if not text:
        return None, None, None

    lowered = text.lower()
    rate: str | None = None
    for hint, value in _PERIOD_HINTS:
        if hint in lowered:
            rate = value
            break

    amounts: list[tuple[float, str | None]] = []
    for match in _AMOUNT_RE.finditer(text):
        raw = match.group("value").replace(",", "").replace(" ", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        if match.group("suffix"):
            value *= 1000
        symbol = (match.group("symbol") or "").strip()
        currency = _CURRENCY_SYMBOLS.get(symbol) or (
            symbol.upper() if symbol.upper() in USD_PER_UNIT else None
        )
        amounts.append((value, currency))

    if not amounts:
        return None, None, rate

    # A range ("$150,000 - $190,000") is represented by its lower bound so the
    # `Amount` filter reads as a floor, matching the user's mental model.
    plausible = [item for item in amounts if item[0] >= 1000] or amounts
    value, currency = min(plausible, key=lambda item: item[0])

    if rate is None:
        # Infer the period from magnitude when the text did not say.
        if value < 500:
            rate = "Hourly"
        elif value < 20000:
            rate = "Monthly"
        else:
            rate = "Yearly"

    if currency is None:
        for code in USD_PER_UNIT:
            if code in text.upper():
                currency = code
                break
    return value, currency, rate

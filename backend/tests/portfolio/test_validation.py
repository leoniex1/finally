"""The shared input rules (`API_CONTRACT.md` §2, §5; `PLAN.md` §8).

These helpers are the single source of truth for both the REST trade bar and
the LLM action executor, so the cases below are deliberately exhaustive
about *rejection*: everything they let through eventually moves cash or
writes a position row.
"""

from __future__ import annotations

import math

import pytest

from app.portfolio.validation import (
    QUANTITY_EPSILON,
    normalize_ticker,
    validate_quantity,
    validate_side,
)


# ── normalize_ticker ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("AAPL", "AAPL"),
        ("aapl", "AAPL"),
        ("  msft  ", "MSFT"),
        ("\tnflx\n", "NFLX"),
        ("V", "V"),
        ("GOOGL", "GOOGL"),
    ],
)
def test_normalize_ticker_trims_and_uppercases(raw: str, expected: str) -> None:
    assert normalize_ticker(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "ABCDEF",  # six characters — one past the limit
        "BRK.B",  # punctuation
        "BRK-B",
        "AA PL",  # interior whitespace survives the strip
        "AA1",  # digits
        "123",
        "aapl!",
        "ÄÄPL",  # non-ASCII letters
    ],
)
def test_normalize_ticker_rejects_bad_formats(raw: str) -> None:
    with pytest.raises(ValueError, match="Invalid ticker"):
        normalize_ticker(raw)


@pytest.mark.parametrize("raw", [None, 42, 3.5, ["AAPL"], {"ticker": "AAPL"}, True])
def test_normalize_ticker_rejects_non_strings(raw: object) -> None:
    with pytest.raises(ValueError, match="Invalid ticker"):
        normalize_ticker(raw)


def test_normalize_ticker_error_echoes_the_normalized_symbol() -> None:
    """The frontend renders `detail` verbatim beside the input the user just
    filled in, so the message has to name the offending symbol."""
    with pytest.raises(ValueError) as excinfo:
        normalize_ticker(" abcdef ")
    assert str(excinfo.value) == "Invalid ticker: 'ABCDEF'"


# ── validate_quantity ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (10, 10.0),
        (10.5, 10.5),
        (0.001, 0.001),
        (QUANTITY_EPSILON, QUANTITY_EPSILON),
        ("3", 3.0),
        (" 2.5 ", 2.5),
        ("1e2", 100.0),
    ],
)
def test_validate_quantity_accepts_positive_finite_numbers(raw: object, expected: float) -> None:
    result = validate_quantity(raw)
    assert result == pytest.approx(expected)
    assert isinstance(result, float)


@pytest.mark.parametrize(
    "raw",
    [
        0,
        0.0,
        -1,
        -0.5,
        "0",
        "-3",
        float("nan"),
        float("inf"),
        float("-inf"),
        "nan",
        "inf",
        "abc",
        "",
        "   ",
        None,
        [1],
        {"quantity": 1},
        True,  # bool is an int subclass; `True` must not fill as one share
        False,
    ],
)
def test_validate_quantity_rejects(raw: object) -> None:
    with pytest.raises(ValueError, match="Invalid quantity"):
        validate_quantity(raw)


def test_validate_quantity_rejects_nan_before_any_comparison() -> None:
    """`NaN` fails every `>` comparison, so an unchecked one slips past the
    cash and share checks downstream and lands in `positions.quantity`."""
    with pytest.raises(ValueError):
        validate_quantity(math.nan)


def test_quantity_epsilon_matches_the_contract() -> None:
    assert QUANTITY_EPSILON == 1e-6


# ── validate_side ────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw", ["buy", "sell"])
def test_validate_side_accepts_the_two_sides(raw: str) -> None:
    assert validate_side(raw) == raw


@pytest.mark.parametrize(
    "raw",
    ["BUY", "Buy", " buy", "buy ", "sel", "short", "", None, 1, ["buy"], "sell_all"],
)
def test_validate_side_rejects_anything_else(raw: object) -> None:
    """Strict by design: both producers are machines (buttons and a
    `Literal["buy","sell"]` structured output), so a near-miss here is a bug
    rather than a user typo to be forgiven."""
    with pytest.raises(ValueError, match="Invalid side"):
        validate_side(raw)

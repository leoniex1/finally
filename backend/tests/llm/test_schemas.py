"""The structured-output schema is the guard rail (`API_CONTRACT.md` §6).

These tests exist because an undeclared enum or an undeclared `gt=0` bound
fails silently downstream: the executor either throws on parse or no-ops an
action the user was just told had happened.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.llm.schemas import LLMResponse, TradeIntent, WatchlistChange


def test_trade_intent_accepts_a_valid_payload() -> None:
    intent = TradeIntent(ticker="AAPL", side="buy", quantity=10)

    assert intent.ticker == "AAPL"
    assert intent.side == "buy"
    assert intent.quantity == 10.0


def test_trade_intent_accepts_lowercase_and_fractional_quantity() -> None:
    intent = TradeIntent(ticker="aapl", side="sell", quantity=0.5)

    assert intent.ticker == "AAPL"
    assert intent.quantity == 0.5


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Buy", "buy"), ("SELL", "sell"), (" buy ", "buy")],
)
def test_trade_intent_folds_side_case(raw: str, expected: str) -> None:
    """A capitalization slip must cost one action at most, never the message.

    `LLMResponse` validation is all-or-nothing: without the fold, a model that
    writes `"Buy"` fails the *whole* response and the user loses the prose
    they were about to read to a `502` (`API_CONTRACT.md` §6). With it, the
    action runs and either applies or fails with a stated reason.
    """
    assert TradeIntent(ticker="AAPL", side=raw, quantity=1).side == expected


@pytest.mark.parametrize("side", ["sell_all", "hold", "", "delete", "b uy"])
def test_trade_intent_rejects_out_of_enum_side(side: str) -> None:
    """Case folding is not the same as accepting anything: a genuinely
    out-of-enum value still fails, which is what keeps the declared enum
    meaningful (`PLAN.md` §9)."""
    with pytest.raises(ValidationError):
        TradeIntent(ticker="AAPL", side=side, quantity=1)


def test_emitted_json_schema_still_declares_the_strict_enum() -> None:
    """The tolerance is a parse-time convenience, not a loosening of the
    contract *sent to the model*. `PLAN.md` §9 requires the enum be declared
    in the schema rather than merely described in the prompt — that is what
    stops the model emitting `"sell_all"` in the first place, and it is the
    `response_format` schema, not the validator, that does it."""
    schema = TradeIntent.model_json_schema()

    assert schema["properties"]["side"]["enum"] == ["buy", "sell"]
    assert schema["properties"]["quantity"]["exclusiveMinimum"] == 0


@pytest.mark.parametrize("quantity", [0, -1, -0.0001])
def test_trade_intent_rejects_non_positive_quantity(quantity: float) -> None:
    with pytest.raises(ValidationError):
        TradeIntent(ticker="AAPL", side="buy", quantity=quantity)


@pytest.mark.parametrize("ticker", ["ABCDEF", "", "BRK.B", "AA PL", "123"])
def test_trade_intent_rejects_malformed_ticker(ticker: str) -> None:
    with pytest.raises(ValidationError):
        TradeIntent(ticker=ticker, side="buy", quantity=1)


def test_watchlist_change_accepts_both_actions() -> None:
    assert WatchlistChange(ticker="PYPL", action="add").action == "add"
    assert WatchlistChange(ticker="NFLX", action="remove").action == "remove"


@pytest.mark.parametrize(("raw", "expected"), [("ADD", "add"), ("Remove", "remove")])
def test_watchlist_change_folds_action_case(raw: str, expected: str) -> None:
    assert WatchlistChange(ticker="PYPL", action=raw).action == expected


@pytest.mark.parametrize("action", ["delete", "drop", "", "unwatch"])
def test_watchlist_change_rejects_out_of_enum_action(action: str) -> None:
    with pytest.raises(ValidationError):
        WatchlistChange(ticker="PYPL", action=action)


def test_llm_response_defaults_to_no_actions() -> None:
    response = LLMResponse(message="Your tech weighting is high.")

    assert response.trades == []
    assert response.watchlist_changes == []


def test_llm_response_action_lists_are_not_shared_between_instances() -> None:
    first = LLMResponse(message="a")
    first.trades.append(TradeIntent(ticker="AAPL", side="buy", quantity=1))

    assert LLMResponse(message="b").trades == []


def test_llm_response_requires_a_message() -> None:
    with pytest.raises(ValidationError):
        LLMResponse(trades=[])


def test_llm_response_parses_a_full_json_payload() -> None:
    response = LLMResponse.model_validate_json(
        """
        {
          "message": "Placing an order to buy 10 AAPL.",
          "trades": [{"ticker": "AAPL", "side": "buy", "quantity": 10}],
          "watchlist_changes": [{"ticker": "PYPL", "action": "add"}]
        }
        """
    )

    assert response.trades[0].ticker == "AAPL"
    assert response.watchlist_changes[0].action == "add"

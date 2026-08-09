"""Every trigger in the `API_CONTRACT.md` §6.1 table.

This table is a contract with integration-tester: the E2E suite drives the
chat panel with these exact substrings. A test per row means a trigger cannot
be renamed or dropped without a red build here first.
"""

from __future__ import annotations

import pytest

from app.llm.mock import MOCK_TRIGGERS, SELL_ALL_QUANTITY, mock_response


def test_buy_100_nvda_requests_an_unaffordable_buy() -> None:
    response = mock_response("buy 100 NVDA")

    assert len(response.trades) == 1
    trade = response.trades[0]
    assert (trade.ticker, trade.side, trade.quantity) == ("NVDA", "buy", 100.0)
    assert response.watchlist_changes == []
    # The fixture that proves the UI contradicts the prose: the message must
    # claim the buy happened even though execution will reject it.
    assert "nvda" in response.message.lower()


def test_buy_1_aapl_requests_an_affordable_buy() -> None:
    response = mock_response("buy 1 AAPL")

    assert len(response.trades) == 1
    trade = response.trades[0]
    assert (trade.ticker, trade.side, trade.quantity) == ("AAPL", "buy", 1.0)


def test_sell_all_requests_more_shares_than_any_seeded_portfolio_holds() -> None:
    response = mock_response("sell all")

    trade = response.trades[0]
    assert (trade.ticker, trade.side) == ("AAPL", "sell")
    assert trade.quantity == SELL_ALL_QUANTITY
    assert trade.quantity > 100_000


def test_add_pypl_requests_a_watchlist_add() -> None:
    response = mock_response("add PYPL")

    assert response.trades == []
    change = response.watchlist_changes[0]
    assert (change.ticker, change.action) == ("PYPL", "add")


def test_remove_nflx_requests_a_watchlist_remove() -> None:
    response = mock_response("remove NFLX")

    change = response.watchlist_changes[0]
    assert (change.ticker, change.action) == ("NFLX", "remove")


def test_buy_zzzz_requests_a_trade_on_an_unpriced_ticker() -> None:
    response = mock_response("buy ZZZZ")

    trade = response.trades[0]
    assert (trade.ticker, trade.side, trade.quantity) == ("ZZZZ", "buy", 1.0)


def test_unmatched_message_returns_prose_and_no_actions() -> None:
    response = mock_response("how is my portfolio doing?")

    assert response.message
    assert response.trades == []
    assert response.watchlist_changes == []


@pytest.mark.parametrize("trigger", [trigger for trigger, _ in MOCK_TRIGGERS])
def test_triggers_match_case_insensitively_inside_a_sentence(trigger: str) -> None:
    embedded = f"Hey FinAlly, please {trigger.upper()} for me right now."

    response = mock_response(embedded)

    assert response.trades or response.watchlist_changes


def test_mock_is_deterministic() -> None:
    assert mock_response("buy 100 nvda") == mock_response("buy 100 NVDA")


@pytest.mark.parametrize("message", ["", None])
def test_empty_message_falls_back_to_prose(message: str | None) -> None:
    response = mock_response(message)  # type: ignore[arg-type]

    assert response.trades == []

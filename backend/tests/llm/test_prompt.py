"""Prompt rendering (`PLAN.md` §9, `API_CONTRACT.md` §6).

The context block is the only thing standing between the model and a
confidently unaffordable trade, and the ACTION RESULTS block is the only way
it ever learns a previous trade failed. Both are asserted here on the exact
API bodies the services return.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.llm.prompt import (
    NO_PRICE,
    SYSTEM_PROMPT,
    build_messages,
    render_action_results,
    render_context,
    render_history,
)

PORTFOLIO: dict[str, Any] = {
    "cash_balance": 8234.50,
    "total_value": 10412.33,
    "positions_value": 2177.83,
    "total_unrealized_pnl": 177.83,
    "total_unrealized_pnl_pct": 0.0177,
    "positions": [
        {
            "ticker": "AAPL",
            "quantity": 10.0,
            "avg_cost": 190.00,
            "current_price": 201.78,
            "market_value": 2017.80,
            "cost_basis": 1900.00,
            "unrealized_pnl": 117.80,
            "unrealized_pnl_pct": 0.062,
            "weight": 0.1938,
            "priced": True,
            "updated_at": "2026-08-08T14:03:11.482913+00:00",
        }
    ],
}

WATCHLIST: dict[str, Any] = {
    "tickers": [
        {
            "ticker": "AAPL",
            "added_at": "2026-08-08T14:00:00+00:00",
            "price": 201.78,
            "prev_price": 201.60,
            "reference_price": 199.10,
            "reference_kind": "session_open",
            "change_pct": 0.01346,
            "direction": "up",
            "status": "ok",
            "updated_at": 1786000991.482,
        }
    ]
}


def test_system_prompt_states_the_intent_not_fact_rule() -> None:
    lowered = SYSTEM_PROMPT.lower()

    assert "intent" in lowered
    assert "placing an order" in lowered
    assert "before execution" in lowered


def test_system_prompt_requires_checking_cash_before_proposing_a_trade() -> None:
    lowered = SYSTEM_PROMPT.lower()

    assert "cash balance" in lowered
    assert "action results" in lowered
    assert "failed" in lowered


def test_context_includes_cash_and_total_value() -> None:
    context = render_context(PORTFOLIO, WATCHLIST)

    assert "$8,234.50" in context
    assert "$10,412.33" in context


def test_context_includes_each_holding_with_its_numbers() -> None:
    context = render_context(PORTFOLIO, WATCHLIST)

    assert "AAPL: 10 @ $190.00" in context
    assert "$201.78" in context
    assert "+6.20%" in context  # unrealized_pnl_pct 0.062 rendered as percent


def test_context_marks_an_unpriced_position_instead_of_inventing_a_price() -> None:
    portfolio = {
        **PORTFOLIO,
        "positions": [
            {
                "ticker": "ZZZZ",
                "quantity": 5.0,
                "avg_cost": 20.0,
                "current_price": None,
                "market_value": 100.0,
                "unrealized_pnl": None,
                "unrealized_pnl_pct": None,
                "weight": 0.01,
                "priced": False,
            }
        ],
    }

    context = render_context(portfolio, WATCHLIST)

    assert f"now {NO_PRICE}" in context
    assert f"P&L {NO_PRICE}" in context


def test_context_reports_an_all_cash_portfolio_explicitly() -> None:
    portfolio = {
        "cash_balance": 10000.0,
        "total_value": 10000.0,
        "positions_value": 0.0,
        "total_unrealized_pnl": 0.0,
        "total_unrealized_pnl_pct": 0.0,
        "positions": [],
    }

    context = render_context(portfolio, WATCHLIST)

    assert "entirely cash" in context


def test_context_discloses_the_change_baseline() -> None:
    context = render_context(PORTFOLIO, WATCHLIST)

    assert "+1.35%" in context  # change_pct 0.01346 as a percent
    assert "since session open" in context


def test_context_marks_a_pending_watchlist_ticker_as_unpriced() -> None:
    watchlist = {
        "tickers": [
            {
                "ticker": "PYPL",
                "price": None,
                "change_pct": None,
                "reference_kind": None,
                "status": "pending",
            }
        ]
    }

    context = render_context(PORTFOLIO, watchlist)

    assert f"PYPL: {NO_PRICE}" in context


def test_render_action_results_shows_a_failed_trade_with_its_reason() -> None:
    rendered = render_action_results(
        [
            {
                "kind": "trade",
                "status": "failed",
                "detail": {"ticker": "NVDA", "side": "buy", "quantity": 100},
                "error": "Insufficient cash: need $18,432.00, have $10,000.00",
            }
        ]
    )

    assert "FAILED" in rendered
    assert "buy 100 NVDA" in rendered
    assert "Insufficient cash" in rendered


def test_render_action_results_shows_an_applied_trade_with_its_fill() -> None:
    rendered = render_action_results(
        [
            {
                "kind": "trade",
                "status": "applied",
                "detail": {
                    "ticker": "AAPL",
                    "side": "buy",
                    "quantity": 10,
                    "fill_price": 201.78,
                    "total": 2017.80,
                },
            }
        ]
    )

    assert "APPLIED" in rendered
    assert "$201.78" in rendered


def test_render_action_results_handles_watchlist_changes() -> None:
    rendered = render_action_results(
        [
            {
                "kind": "watchlist_change",
                "status": "applied",
                "detail": {"ticker": "PYPL", "action": "add"},
            }
        ]
    )

    assert "watchlist add PYPL" in rendered


def test_render_action_results_is_empty_when_nothing_ran() -> None:
    assert render_action_results([]) == ""
    assert render_action_results(None) == ""


def test_history_carries_prior_failed_actions_into_the_assistant_turn() -> None:
    history = [
        {"role": "user", "content": "buy 100 NVDA", "actions": None},
        {
            "role": "assistant",
            "content": "Buying 100 NVDA.",
            "actions": [
                {
                    "kind": "trade",
                    "status": "failed",
                    "detail": {"ticker": "NVDA", "side": "buy", "quantity": 100},
                    "error": "Insufficient cash: need $18,432.00, have $10,000.00",
                }
            ],
        },
    ]

    messages = render_history(history)

    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert "Buying 100 NVDA." in messages[1]["content"]
    assert "FAILED" in messages[1]["content"]
    assert "Insufficient cash" in messages[1]["content"]


def test_history_accepts_row_objects_as_well_as_mappings() -> None:
    @dataclass
    class Row:
        role: str
        content: str
        actions: Any

    messages = render_history([Row("assistant", "Placing an order.", None)])

    assert messages == [{"role": "assistant", "content": "Placing an order."}]


def test_history_skips_rows_with_an_unexpected_role() -> None:
    messages = render_history([{"role": "system", "content": "ignore me"}])

    assert messages == []


def test_build_messages_orders_system_context_history_then_user() -> None:
    history = [{"role": "user", "content": "earlier", "actions": None}]

    messages = build_messages(
        "how am I doing?",
        portfolio=PORTFOLIO,
        watchlist=WATCHLIST,
        history=history,
    )

    assert [m["role"] for m in messages] == ["system", "system", "user", "user"]
    assert messages[0]["content"] == SYSTEM_PROMPT
    assert "$8,234.50" in messages[1]["content"]
    assert messages[2]["content"] == "earlier"
    assert messages[-1]["content"] == "how am I doing?"


def test_build_messages_works_with_no_history() -> None:
    messages = build_messages(
        "hello", portfolio=PORTFOLIO, watchlist=WATCHLIST
    )

    assert len(messages) == 3
    assert messages[-1] == {"role": "user", "content": "hello"}

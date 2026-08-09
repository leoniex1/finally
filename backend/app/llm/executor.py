"""Executing the model's requested actions (`API_CONTRACT.md` §3.8, §6).

This module exists because of a specific failure mode. The model composes its
prose *before* anything runs, and there is only one LLM call per message
(`PLAN.md` §9), so the message can confidently announce a trade that the
executor then rejects. With $10,000 cash, "buy 100 NVDA" produces "Done —
bought 100 NVDA" and an $18k order that cannot fill. The `actions` array
built here is the authoritative record, and the UI renders it even when it
contradicts the message above it.

Two rules the whole module is arranged around:

1. **Every action runs through the same validations as a manual request.**
   `execute_trade`, `add_ticker` and `remove_ticker` are called directly —
   not over HTTP, and never re-implemented. A rule that lived in a route
   handler would be a rule the chat path silently did not get.
2. **One failure does not abort the rest.** Actions are independent; a
   rejected trade must not swallow the watchlist change that followed it.
"""

from __future__ import annotations

import logging
from typing import Any

from ..market_data.cache import PriceCache
from ..portfolio.errors import ServiceError
from ..portfolio.service import execute_trade
from ..portfolio.watchlist_service import add_ticker, remove_ticker
from .schemas import LLMResponse, TradeIntent, WatchlistChange

logger = logging.getLogger("app.llm.executor")


async def execute_actions(
    db_path: str, cache: PriceCache, response: LLMResponse
) -> list[dict[str, Any]]:
    """Run every action in `response`, returning the §3.8 `actions` array.

    Trades run before watchlist changes, matching the field order of the
    schema in `PLAN.md` §9. The ordering is documented rather than clever:
    it deliberately does *not* try to make "add PYPL then buy PYPL" work in
    one turn. A freshly added ticker has no price until the driver's next
    cycle, so that buy is refused with `409` whichever order runs first —
    and refusing it is correct. Filling it would mean inventing a price.

    Never raises for a rejected action: a rejection is a result, and its
    reason is what the user needs to see.
    """
    actions: list[dict[str, Any]] = []

    for trade in response.trades:
        actions.append(await _run_trade(db_path, cache, trade))

    for change in response.watchlist_changes:
        actions.append(await _run_watchlist_change(db_path, cache, change))

    return actions


async def _run_trade(
    db_path: str, cache: PriceCache, intent: TradeIntent
) -> dict[str, Any]:
    """One trade, as an `actions` entry.

    The applied `detail` reports the trade **as executed**, not as requested:
    the quantity comes back from the fill, so a full liquidation that
    absorbed a float residue (`PLAN.md` §7) reports the amount that actually
    sold rather than the slightly smaller number the model asked for.
    """
    requested = {
        "ticker": intent.ticker,
        "side": intent.side,
        "quantity": intent.quantity,
    }
    try:
        result = await execute_trade(
            db_path, cache, intent.ticker, intent.quantity, intent.side
        )
    except ServiceError as exc:
        logger.info("chat trade rejected: %s", exc.detail)
        return {
            "kind": "trade",
            "status": "failed",
            "detail": requested,
            "error": exc.detail,
        }

    filled = result["trade"]
    return {
        "kind": "trade",
        "status": "applied",
        "detail": {
            "ticker": filled["ticker"],
            "side": filled["side"],
            "quantity": filled["quantity"],
            "fill_price": filled["price"],
            "total": filled["total"],
        },
    }


async def _run_watchlist_change(
    db_path: str, cache: PriceCache, change: WatchlistChange
) -> dict[str, Any]:
    """One watchlist add/remove, as an `actions` entry."""
    detail = {"ticker": change.ticker, "action": change.action}
    try:
        if change.action == "add":
            await add_ticker(db_path, cache, change.ticker)
        else:
            await remove_ticker(db_path, change.ticker)
    except ServiceError as exc:
        logger.info("chat watchlist change rejected: %s", exc.detail)
        return {
            "kind": "watchlist_change",
            "status": "failed",
            "detail": detail,
            "error": exc.detail,
        }

    return {"kind": "watchlist_change", "status": "applied", "detail": detail}

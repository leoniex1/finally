"""Deterministic mock responses for `LLM_MOCK=true` (`API_CONTRACT.md` §6.1).

Mock mode replaces **only** the LLM call. The response it returns goes through
the real executor and the real result-reporting path, so an E2E test exercises
everything except the network hop to OpenRouter.

**The trigger table below is a contract with integration-tester.** The E2E
suite selects on these substrings; changing one silently breaks their suite.
Do not edit a trigger without messaging integration-tester and the team lead.

The messages are written the way the real model is prompted to write — as
intent, not as completed fact — with one deliberate exception: the
`buy 100 nvda` case claims success it will not get. That is the whole point of
it. It is the fixture that proves the UI contradicts the prose when the prose
is wrong (`PLAN.md` §9, "Reporting Results Honestly").
"""

from __future__ import annotations

from typing import Callable

from .schemas import LLMResponse, TradeIntent, WatchlistChange

#: Large enough that no plausible seeded portfolio holds it, so the `sell all`
#: trigger reliably fails validation rule 6 (insufficient shares) rather than
#: succeeding by accident on a portfolio that happens to hold enough.
SELL_ALL_QUANTITY = 1_000_000.0


def _buy_100_nvda() -> LLMResponse:
    return LLMResponse(
        message="Buying 100 NVDA to lift your tech weighting.",
        trades=[TradeIntent(ticker="NVDA", side="buy", quantity=100)],
    )


def _buy_1_aapl() -> LLMResponse:
    return LLMResponse(
        message="Placing an order to buy 1 AAPL — a small, low-risk starter position.",
        trades=[TradeIntent(ticker="AAPL", side="buy", quantity=1)],
    )


def _sell_all() -> LLMResponse:
    return LLMResponse(
        message="Placing an order to close out the AAPL position entirely.",
        trades=[
            TradeIntent(ticker="AAPL", side="sell", quantity=SELL_ALL_QUANTITY)
        ],
    )


def _add_pypl() -> LLMResponse:
    return LLMResponse(
        message="Adding PYPL to your watchlist so you can track it.",
        watchlist_changes=[WatchlistChange(ticker="PYPL", action="add")],
    )


def _remove_nflx() -> LLMResponse:
    return LLMResponse(
        message="Removing NFLX from your watchlist.",
        watchlist_changes=[WatchlistChange(ticker="NFLX", action="remove")],
    )


def _buy_zzzz() -> LLMResponse:
    return LLMResponse(
        message="Placing an order to buy 1 ZZZZ.",
        trades=[TradeIntent(ticker="ZZZZ", side="buy", quantity=1)],
    )


def _default() -> LLMResponse:
    return LLMResponse(
        message=(
            "Your portfolio is concentrated in large-cap tech, which moves "
            "together — a drawdown in one name tends to arrive with the "
            "others. Cash is your only diversifier right now. Consider "
            "trimming the largest weight and adding a name outside the "
            "sector before adding more exposure."
        )
    )


#: Ordered: the first trigger found in the (lower-cased) user message wins.
#: Order is explicit rather than dict-insertion-incidental because these are
#: substring matches and a future trigger could be a prefix of another.
MOCK_TRIGGERS: tuple[tuple[str, Callable[[], LLMResponse]], ...] = (
    ("buy 100 nvda", _buy_100_nvda),
    ("buy 1 aapl", _buy_1_aapl),
    ("sell all", _sell_all),
    ("add pypl", _add_pypl),
    ("remove nflx", _remove_nflx),
    ("buy zzzz", _buy_zzzz),
)


def mock_response(user_message: str) -> LLMResponse:
    """Return the mock response for `user_message` (§6.1).

    Matching is case-insensitive substring containment, so a test can send
    "please buy 100 NVDA for me" and still hit the trigger. Anything with no
    trigger gets analysis-style prose and no actions.
    """
    haystack = (user_message or "").lower()
    for trigger, build in MOCK_TRIGGERS:
        if trigger in haystack:
            return build()
    return _default()

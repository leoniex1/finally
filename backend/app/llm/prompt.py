"""Prompt construction for the chat assistant (`PLAN.md` §9).

Two jobs: the system prompt, and rendering live state (portfolio, watchlist,
prior action results) into compact text the model can actually use.

"Compact" is load-bearing. The context is rebuilt from scratch on every turn
and prepended to the whole conversation history, so a verbose JSON dump of
the portfolio would be re-sent on every message and crowd out the history
that tells the model a previous trade failed.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

#: What the UI renders for an unpriced ticker is an em dash; the prompt uses
#: a word instead, so the model never quotes a punctuation mark as a price.
NO_PRICE = "unpriced"

SYSTEM_PROMPT = """\
You are FinAlly, an AI trading assistant embedded in a simulated trading \
workstation. The user holds a virtual portfolio funded with fake money — \
there are no real stakes, no fees, and every order is a market order that \
fills instantly at the current price.

Your job:
- Analyze portfolio composition, risk concentration, and P&L.
- Suggest trades, always with the reasoning behind them.
- Execute trades when the user asks for them or agrees to your suggestion.
- Manage the watchlist proactively — add tickers you are discussing, remove \
ones that are no longer relevant.
- Be concise and data-driven. Cite the actual numbers you were given. Short \
paragraphs, no filler, no disclaimers about not being a financial advisor.

Hard rules:
- Always respond with valid JSON matching the required schema. Never wrap it \
in markdown fences and never add prose outside the JSON.
- Describe requested trades as INTENT, never as completed fact. Write \
"Placing an order to buy 10 AAPL" — never "Bought 10 AAPL." Your message is \
written before execution happens and the order may still be rejected; the \
application reports the real outcome to the user separately.
- Check the cash balance and holdings in the PORTFOLIO section below before \
proposing any trade. A buy needs quantity x price <= cash balance; a sell \
needs the shares to already be held. If an order does not fit, say so and \
propose one that does, instead of attempting it anyway.
- Never trade a ticker whose price shows as "{no_price}" — there is no price \
to fill against and the order will be refused.
- Read the ACTION RESULTS lines in the conversation history. They are the \
authoritative record of what actually happened. An action marked FAILED did \
not happen: the shares were never bought, the cash was never spent, and the \
portfolio figures below already reflect that. Never repeat a claim that a \
failed trade succeeded.
- Put every trade you want executed in `trades` and every watchlist edit in \
`watchlist_changes`. Describing an action in `message` alone does nothing — \
the arrays are what the application executes. Conversely, do not populate \
them for a hypothetical you are merely discussing.
""".format(no_price=NO_PRICE)


def build_messages(
    user_message: str,
    *,
    portfolio: Mapping[str, Any],
    watchlist: Mapping[str, Any],
    history: Sequence[Any] = (),
) -> list[dict[str, str]]:
    """Assemble the full message list for one turn (`PLAN.md` §9, steps 1-3).

    `portfolio` is the `/api/portfolio` body (§3.2) and `watchlist` the
    `/api/watchlist` body (§3.5) — the caller passes the live service results,
    so the model always reasons about the same numbers the UI is showing.

    `history` is the stored `chat_messages` rows, oldest first, *excluding*
    the message being answered. The context block is placed after the system
    prompt but before the history so the freshest state is not buried, and
    each historical assistant turn carries its action results inline.
    """
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": render_context(portfolio, watchlist)},
        *render_history(history),
        {"role": "user", "content": user_message},
    ]


def render_context(
    portfolio: Mapping[str, Any], watchlist: Mapping[str, Any]
) -> str:
    """Render the live portfolio and watchlist as compact prompt text."""
    return "\n".join(
        [
            "PORTFOLIO",
            _render_portfolio(portfolio),
            "",
            "WATCHLIST",
            _render_watchlist(watchlist),
        ]
    )


def _render_portfolio(portfolio: Mapping[str, Any]) -> str:
    lines = [
        f"Cash balance: {_money(portfolio.get('cash_balance'))}",
        f"Positions value: {_money(portfolio.get('positions_value'))}",
        f"Total portfolio value: {_money(portfolio.get('total_value'))}",
        "Total unrealized P&L: "
        f"{_money(portfolio.get('total_unrealized_pnl'))} "
        f"({_pct(portfolio.get('total_unrealized_pnl_pct'))})",
    ]

    positions = portfolio.get("positions") or []
    if not positions:
        lines.append("Positions: none — the portfolio is entirely cash.")
        return "\n".join(lines)

    lines.append(
        "Positions (ticker, qty, avg cost, current price, market value, "
        "unrealized P&L, weight):"
    )
    for position in positions:
        priced = position.get("priced", True)
        lines.append(
            "  {ticker}: {qty:g} @ {avg} | now {price} | value {value} | "
            "P&L {pnl} ({pnl_pct}) | weight {weight}".format(
                ticker=position.get("ticker", "?"),
                qty=float(position.get("quantity") or 0.0),
                avg=_money(position.get("avg_cost")),
                price=_money(position.get("current_price")) if priced else NO_PRICE,
                value=_money(position.get("market_value")),
                pnl=_money(position.get("unrealized_pnl")) if priced else NO_PRICE,
                pnl_pct=_pct(position.get("unrealized_pnl_pct")) if priced else NO_PRICE,
                weight=_weight(position.get("weight")),
            )
        )
    return "\n".join(lines)


def _render_watchlist(watchlist: Mapping[str, Any]) -> str:
    tickers = watchlist.get("tickers") or []
    if not tickers:
        lines = ["  (empty)"]
    else:
        lines = []
        for row in tickers:
            # Mirrors the cache's own rule (§9): a price is usable only when
            # the status is ok *and* a price is actually present.
            usable = row.get("status") == "ok" and row.get("price") is not None
            ticker = row.get("ticker", "?")
            if not usable:
                lines.append(f"  {ticker}: {NO_PRICE} (no tick from the data feed yet)")
                continue
            lines.append(
                "  {ticker}: {price} ({change} {baseline})".format(
                    ticker=ticker,
                    price=_money(row.get("price")),
                    change=_pct(row.get("change_pct")),
                    baseline=_baseline_label(row.get("reference_kind")),
                )
            )
    lines.append(
        "A ticker not listed above can still be traded and can be added to "
        "the watchlist, but it has no price until the data feed picks it up."
    )
    return "\n".join(lines)


def render_history(history: Sequence[Any]) -> list[dict[str, str]]:
    """Turn stored `chat_messages` rows into LiteLLM message dicts.

    Assistant rows carry their stored `actions` appended as an ACTION RESULTS
    block. That block is the whole mechanism by which the model learns a
    trade failed: there is one LLM call per user message and the model's
    prose was written before execution, so without this the next turn would
    happily build on a fill that never happened (`PLAN.md` §9).

    Rows are accepted as mappings or as objects with the attributes, so this
    works with a `ChatRow` dataclass or its `to_dict()` without either side
    of the boundary having to care.
    """
    messages: list[dict[str, str]] = []
    for row in history:
        role = _field(row, "role")
        content = _field(row, "content") or ""
        if role not in ("user", "assistant"):
            continue
        if role == "assistant":
            results = render_action_results(_field(row, "actions"))
            if results:
                content = f"{content}\n{results}"
        messages.append({"role": role, "content": content})
    return messages


def render_action_results(actions: Any) -> str:
    """Render a stored `actions` array (§3.8) as ACTION RESULTS lines."""
    if not actions:
        return ""
    lines = ["ACTION RESULTS (authoritative — what the application actually did):"]
    for action in actions:
        lines.append(f"  {_render_action(action)}")
    return "\n".join(lines)


def _render_action(action: Mapping[str, Any]) -> str:
    detail = action.get("detail") or {}
    kind = action.get("kind")
    ticker = detail.get("ticker", "?")

    if kind == "trade":
        summary = (
            f"{detail.get('side', '?')} {float(detail.get('quantity') or 0.0):g} {ticker}"
        )
        if action.get("status") == "applied":
            summary += (
                f" filled at {_money(detail.get('fill_price'))}"
                f" for {_money(detail.get('total'))}"
            )
    else:
        summary = f"watchlist {detail.get('action', '?')} {ticker}"

    if action.get("status") == "applied":
        return f"APPLIED: {summary}"
    return f"FAILED: {summary} — {action.get('error') or 'no reason given'}"


def _field(row: Any, name: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(name)
    return getattr(row, name, None)


def _money(value: Any) -> str:
    if value is None:
        return NO_PRICE
    return f"${value:,.2f}"


def _pct(value: Any) -> str:
    """`*_pct` fields are fractions on the wire (§2); the model reads percent.

    Signed, because every caller of this is a change or a return, where the
    direction is the point.
    """
    if value is None:
        return NO_PRICE
    return f"{value * 100:+.2f}%"


def _weight(value: Any) -> str:
    """Unsigned: a portfolio weight is a share of the total, never negative,
    and a leading `+` on it reads as a gain."""
    if value is None:
        return NO_PRICE
    return f"{value * 100:.2f}%"


def _baseline_label(reference_kind: Any) -> str:
    if reference_kind == "prev_close":
        return "since previous close"
    if reference_kind == "session_open":
        return "since session open"
    return "baseline unknown"

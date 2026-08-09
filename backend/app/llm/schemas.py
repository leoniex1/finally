"""Pydantic models backing the LLM's structured output (`API_CONTRACT.md` §6).

The enums and the `gt=0` bound are declared *in the model*, not merely
described in the prompt. Left undeclared, the model can plausibly emit
`"delete"`, `"sell_all"`, or a negative quantity, and the executor either
throws on parse or silently no-ops an action the user was just told had
happened (`PLAN.md` §9).

The ticker pattern is deliberately case-insensitive here: the model routinely
writes `aapl`. Normalization to the canonical upper-case form is not this
layer's job — it belongs to the one shared helper,
`app.portfolio.validation.normalize_ticker`, which the executor calls. Two
copies of that rule would drift.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, Field

#: Same shape as `app.portfolio.validation`'s rule, applied pre-normalization.
TICKER_PATTERN = r"^[A-Za-z]{1,5}$"


def _lower(value: Any) -> Any:
    """Fold a string enum value to lower case before the `Literal` check.

    The JSON schema sent to the model still declares the strict enum, so
    `PLAN.md` §9's "declared in the Pydantic model, not merely described in
    the prompt" holds. This only decides what happens when the model ignores
    it and writes `"Buy"`.

    Without the fold, that single capital letter fails `LLMResponse`
    validation, which fails the *whole response* — taking the message the
    user was about to read to a `502` along with it. With it, the action
    simply runs and either applies or fails with a stated reason, which is
    the honest-reporting behaviour §9 is built around.

    The tolerance stops here. `app.portfolio.validation.validate_side` is
    strict and does not fold: by the time a value reaches it, it came from a
    UI button or from this already-normalized enum, so a near-miss there is a
    bug worth surfacing rather than a typo worth absorbing.
    """
    return value.strip().lower() if isinstance(value, str) else value


def _upper(value: Any) -> Any:
    """Uppercase a ticker before the pattern check, so `"aapl "` — which the
    model writes routinely — is not a validation failure."""
    return value.strip().upper() if isinstance(value, str) else value


Ticker = Annotated[str, BeforeValidator(_upper), Field(pattern=TICKER_PATTERN)]
Side = Annotated[Literal["buy", "sell"], BeforeValidator(_lower)]
WatchlistAction = Annotated[Literal["add", "remove"], BeforeValidator(_lower)]


class TradeIntent(BaseModel):
    """A trade the model wants executed. Intent only — the model writes this
    before execution runs, so it is a request, never a record of a fill."""

    ticker: Ticker
    side: Side
    quantity: float = Field(gt=0)


class WatchlistChange(BaseModel):
    """An add/remove the model wants applied to the watchlist."""

    ticker: Ticker
    action: WatchlistAction


class LLMResponse(BaseModel):
    """The complete structured response for one user message.

    `message` is the prose shown to the user. It is composed *before* the
    actions run and is therefore not evidence that anything succeeded — the
    `actions` array the executor builds from `trades` and `watchlist_changes`
    is the authoritative record (`PLAN.md` §9).
    """

    message: str
    trades: list[TradeIntent] = Field(default_factory=list)
    watchlist_changes: list[WatchlistChange] = Field(default_factory=list)

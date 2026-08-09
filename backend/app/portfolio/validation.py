"""Input rules for tickers, quantities and sides — the single source of
truth (`API_CONTRACT.md` §2, §5; `PLAN.md` §8).

Every entry point uses these helpers: the trade bar's REST route, the
watchlist routes, and the LLM action executor, which calls the portfolio
services directly rather than over HTTP. A second copy of these rules
anywhere would let the chat path and the manual path drift apart, which is
exactly the failure `PLAN.md` §9 is written to prevent.

Each helper raises `ValueError` whose message *is* the user-facing `detail`
string from the §3.3 table. Callers wrap it (`TradeError` / `WatchlistError`)
and the frontend renders it verbatim in an inline error slot, so these
strings are written for a human, not for a log.
"""

from __future__ import annotations

import math
import re
from typing import Literal

#: A sell that would leave less than this behind closes the position instead
#: (`PLAN.md` §7). `quantity` is a REAL, so "sell everything" arithmetic can
#: leave a residue like 2e-13; the epsilon absorbs it so the positions table
#: never shows a phantom 0-share holding.
QUANTITY_EPSILON = 1e-6

#: Applied *after* normalization, so `" aapl "` is valid and `"ABCDEF"` is
#: not (`API_CONTRACT.md` §2).
TICKER_PATTERN = re.compile(r"^[A-Z]{1,5}$")

Side = Literal["buy", "sell"]

_VALID_SIDES: frozenset[str] = frozenset({"buy", "sell"})


def normalize_ticker(raw: object) -> str:
    """Trim, uppercase, and validate against `^[A-Z]{1,5}$`.

    Raises `ValueError` — never returns an invalid symbol. Rejecting here is
    what keeps a typo out of the tracked set, where it would otherwise sit
    forever as an `unavailable` ticker the poller keeps retrying.
    """
    if not isinstance(raw, str):
        raise ValueError(f"Invalid ticker: {raw!r}")

    ticker = raw.strip().upper()
    if not TICKER_PATTERN.fullmatch(ticker):
        # Echo the normalized form: the user typed `abcdef`, and reading
        # "Invalid ticker: 'ABCDEF'" next to the field they just filled in
        # is clearer than echoing their exact keystrokes back at them.
        raise ValueError(f"Invalid ticker: {ticker!r}")
    return ticker


def validate_quantity(raw: object) -> float:
    """Coerce to a finite, strictly positive float.

    Rejects `None`, booleans, non-numeric strings, zero, negatives, `NaN`
    and infinities. Booleans are excluded explicitly because `bool` is a
    subclass of `int` in Python — `True` would otherwise fill as one share.
    `NaN` matters too: it fails every `>` comparison, so an unchecked `NaN`
    slips past the cash and share checks below and then writes garbage into
    `positions.quantity`.
    """
    if isinstance(raw, bool) or raw is None:
        raise ValueError("Invalid quantity: must be a positive number")

    if isinstance(raw, (int, float)):
        quantity = float(raw)
    elif isinstance(raw, str):
        try:
            quantity = float(raw.strip())
        except ValueError:
            raise ValueError("Invalid quantity: must be a positive number") from None
    else:
        raise ValueError("Invalid quantity: must be a positive number")

    if not math.isfinite(quantity) or quantity <= 0:
        raise ValueError("Invalid quantity: must be a positive number")
    return quantity


def validate_side(raw: object) -> Side:
    """Accept exactly `"buy"` or `"sell"` (`PLAN.md` §8).

    Deliberately strict rather than case-folding: both producers are
    machines — the trade bar's buttons and the LLM's `Literal["buy","sell"]`
    structured output — so anything else arriving here is a bug or a
    malformed response, and guessing at intent on the way to moving money
    (however simulated) is the wrong instinct.
    """
    if not isinstance(raw, str) or raw not in _VALID_SIDES:
        raise ValueError("Invalid side: must be 'buy' or 'sell'")
    return raw  # type: ignore[return-value]

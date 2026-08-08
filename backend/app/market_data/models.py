"""Shared data model for the market data subsystem.

`CacheEntry` is a plain (non-Pydantic) dataclass because it is mutated in a
hot loop (every ~500ms per tracked ticker) and never crosses a process
boundary directly — only `to_sse_event()` / the history endpoint serialize
it. See `PLAN.md` §6.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Deque, Literal, Optional

#: ~ an hour of simulator ticks at 500ms cadence, per PLAN.md §6.
HISTORY_MAXLEN = 2000


class TickerStatus(str, Enum):
    PENDING = "pending"  # tracked, no tick has arrived yet
    OK = "ok"  # has a live price
    UNAVAILABLE = "unavailable"  # source has no data for this ticker


class ReferenceKind(str, Enum):
    PREV_CLOSE = "prev_close"
    SESSION_OPEN = "session_open"


Direction = Literal["up", "down", "flat"]


@dataclass(frozen=True, slots=True)
class PricePoint:
    """One point in a ticker's history ring buffer."""

    t: float  # unix epoch seconds
    price: float


@dataclass(slots=True)
class CacheEntry:
    """Everything the cache knows about one tracked ticker."""

    ticker: str
    price: Optional[float] = None
    prev_price: Optional[float] = None
    reference_price: Optional[float] = None
    reference_kind: Optional[ReferenceKind] = None
    updated_at: float = 0.0  # epoch seconds of the last *change* to `price`
    status: TickerStatus = TickerStatus.PENDING
    history: Deque[PricePoint] = field(default_factory=lambda: deque(maxlen=HISTORY_MAXLEN))

    @property
    def change_pct(self) -> Optional[float]:
        if self.price is None or not self.reference_price:
            return None
        return (self.price - self.reference_price) / self.reference_price

    @property
    def direction(self) -> Direction:
        if self.price is None or self.prev_price is None or self.price == self.prev_price:
            return "flat"
        return "up" if self.price > self.prev_price else "down"

    def to_sse_event(self) -> dict:
        return {
            "ticker": self.ticker,
            "price": self.price,
            "prev_price": self.prev_price,
            "reference_price": self.reference_price,
            "reference_kind": self.reference_kind.value if self.reference_kind else None,
            "change_pct": self.change_pct,
            "direction": self.direction,
            "status": self.status.value,
            "updated_at": self.updated_at,
        }

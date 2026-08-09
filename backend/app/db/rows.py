"""Row types returned by the repositories (`API_CONTRACT.md` §4.2).

Frozen dataclasses rather than raw `aiosqlite.Row` objects: a `Row` is a
tuple that also happens to support name lookup, so it silently survives a
column reorder and gives no type information to the services built on top.
These are immutable because a repository row is a snapshot of what the
database held at read time — mutating one would look like a write and not be
one.

**`to_dict()` is the API-facing shape, not the column list.** `user_id` is
an internal single-user artefact (`API_CONTRACT.md` §2) and never appears in
a response, and each row emits exactly the fields the contract's JSON asks
for, so a route can hand the dict straight to FastAPI. The full column set,
`id` and `user_id` included, is always available as attributes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True, slots=True)
class WatchlistRow:
    """A row of `watchlist`."""

    id: str
    user_id: str
    ticker: str
    added_at: str

    def to_dict(self) -> dict[str, Any]:
        """The database half of a `GET /api/watchlist` entry
        (`API_CONTRACT.md` §3.5). The route merges `CacheEntry.to_sse_event()`
        over this, which is why the keys here deliberately do not collide with
        any market-data field."""
        return {"ticker": self.ticker, "added_at": self.added_at}


@dataclass(frozen=True, slots=True)
class PositionRow:
    """A row of `positions` — an *open* holding. A closed position has no
    row at all (`PLAN.md` §7), so `None` from the repository means flat, not
    zero shares."""

    id: str
    user_id: str
    ticker: str
    quantity: float
    avg_cost: float
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        """The `position` object of a trade response (`API_CONTRACT.md` §3.3).
        Valuation fields (`current_price`, `unrealized_pnl`, `weight`) are the
        portfolio service's job — they need the price cache, which this layer
        knows nothing about."""
        return {
            "ticker": self.ticker,
            "quantity": self.quantity,
            "avg_cost": self.avg_cost,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class TradeRow:
    """A row of the append-only `trades` log."""

    id: str
    user_id: str
    ticker: str
    side: str
    quantity: float
    price: float
    executed_at: str

    @property
    def total(self) -> float:
        """Cash moved by this fill. Derived rather than stored so it can never
        disagree with `quantity * price`."""
        return self.quantity * self.price

    def to_dict(self) -> dict[str, Any]:
        """The `trade` object of a trade response (`API_CONTRACT.md` §3.3),
        `total` included."""
        return {
            "id": self.id,
            "ticker": self.ticker,
            "side": self.side,
            "quantity": self.quantity,
            "price": self.price,
            "total": self.total,
            "executed_at": self.executed_at,
        }


@dataclass(frozen=True, slots=True)
class SnapshotRow:
    """A row of `portfolio_snapshots` — one point on the P&L chart."""

    id: str
    user_id: str
    total_value: float
    recorded_at: str

    def to_dict(self) -> dict[str, Any]:
        """One element of `points` in `GET /api/portfolio/history`
        (`API_CONTRACT.md` §3.4)."""
        return {"recorded_at": self.recorded_at, "total_value": self.total_value}


@dataclass(frozen=True, slots=True)
class ChatRow:
    """A row of `chat_messages`.

    `actions` is the decoded Python object, never the stored JSON string —
    `chat_repo` owns that encoding on both sides (`API_CONTRACT.md` §4.2). It
    is `None` for user messages, and for assistant messages it is the
    per-action results array of §3.8, *including failures*: that record is
    what tells the model on the next turn that a trade it announced did not
    actually happen.
    """

    id: str
    user_id: str
    role: str
    content: str
    actions: Optional[list[Any]]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        """One element of `messages` in `GET /api/chat/history`
        (`API_CONTRACT.md` §3.9)."""
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "actions": self.actions,
            "created_at": self.created_at,
        }

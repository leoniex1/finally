"""The database access layer (`API_CONTRACT.md` §4).

Everything that touches SQLite lives here. No route handler and no service
opens a connection or writes SQL directly — they call `connect()` /
`transaction()` and the repository functions below.

The repository modules are exported rather than their individual functions:
`positions_repo.get_position(db, "AAPL")` says at the call site which table
is being read, where a bare `get_position` would not.
"""

from . import (
    chat_repo,
    positions_repo,
    profile_repo,
    snapshots_repo,
    trades_repo,
    watchlist_repo,
)
from .connection import connect, transaction
from .init import DEFAULT_CASH_BALANCE, DEFAULT_USER_ID, DEFAULT_WATCHLIST, init_db, utc_now_iso
from .rows import ChatRow, PositionRow, SnapshotRow, TradeRow, WatchlistRow

__all__ = [
    "init_db",
    "DEFAULT_WATCHLIST",
    "DEFAULT_CASH_BALANCE",
    "DEFAULT_USER_ID",
    "utc_now_iso",
    "connect",
    "transaction",
    "WatchlistRow",
    "PositionRow",
    "TradeRow",
    "SnapshotRow",
    "ChatRow",
    "watchlist_repo",
    "positions_repo",
    "trades_repo",
    "profile_repo",
    "snapshots_repo",
    "chat_repo",
]

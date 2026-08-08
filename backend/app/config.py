"""Environment-driven configuration for the market data subsystem.

This is deliberately scoped to what `market_data/` needs (PLAN.md §5/§6) —
the rest of the application's settings (DB path, OpenRouter key, etc.) belong
to a broader `Settings` object that a future backend module will own.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_MASSIVE_POLL_INTERVAL_SECONDS = 15.0
DEFAULT_DB_PATH = "db/finally.db"


@dataclass(frozen=True)
class Settings:
    massive_api_key: str = ""
    massive_poll_interval_seconds: float = DEFAULT_MASSIVE_POLL_INTERVAL_SECONDS
    #: Where the SQLite file lives. `TrackedSetProvider` reads the watchlist
    #: and positions tables from it every driver cycle (`PLAN.md` §6, §11).
    db_path: str = DEFAULT_DB_PATH

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            massive_api_key=os.environ.get("MASSIVE_API_KEY", "").strip(),
            massive_poll_interval_seconds=float(
                os.environ.get(
                    "MASSIVE_POLL_INTERVAL_SECONDS",
                    str(DEFAULT_MASSIVE_POLL_INTERVAL_SECONDS),
                )
            ),
            db_path=os.environ.get("DB_PATH", DEFAULT_DB_PATH),
        )

    @property
    def use_massive(self) -> bool:
        return bool(self.massive_api_key)

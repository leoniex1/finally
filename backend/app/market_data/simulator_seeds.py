"""Seed data for the market simulator (`MARKET_SIMULATOR.md` §3).

`spec_for` is a pure function — same input, same output, forever — which is
what makes unknown-ticker seeding deterministic across restarts with no
persisted state.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

DEFAULT_MU = 0.05
DEFAULT_SIGMA = 0.35


@dataclass(frozen=True)
class SeedSpec:
    price: float
    sector: str
    mu: float = DEFAULT_MU
    sigma: float = DEFAULT_SIGMA


KNOWN_TICKERS: dict[str, SeedSpec] = {
    "AAPL": SeedSpec(190.0, "tech"),
    "GOOGL": SeedSpec(175.0, "tech"),
    "MSFT": SeedSpec(420.0, "tech"),
    "AMZN": SeedSpec(185.0, "tech"),
    "TSLA": SeedSpec(250.0, "auto", sigma=0.55),
    "NVDA": SeedSpec(120.0, "tech", sigma=0.50),
    "META": SeedSpec(500.0, "tech"),
    "JPM": SeedSpec(210.0, "finance", sigma=0.25),
    "V": SeedSpec(275.0, "finance", sigma=0.20),
    "NFLX": SeedSpec(650.0, "media", sigma=0.40),
}

SECTORS = ("tech", "finance", "auto", "media", "other")


def seed_for_unknown_ticker(ticker: str) -> SeedSpec:
    """Deterministic seed price in [$20, $400) derived from a hash of the
    symbol, so a given symbol always starts at the same price, across
    restarts, with no persisted state."""
    digest = hashlib.sha256(ticker.encode("utf-8")).digest()
    n = int.from_bytes(digest[:4], "big")  # 0 .. 2**32-1, uniform
    price = 20.0 + (n % 38000) / 100.0  # 20.00 .. 399.99, cents-resolution
    sector = SECTORS[digest[4] % len(SECTORS)]  # a 5th independent byte picks the sector
    return SeedSpec(price=price, sector=sector)


def spec_for(ticker: str) -> SeedSpec:
    return KNOWN_TICKERS.get(ticker) or seed_for_unknown_ticker(ticker)

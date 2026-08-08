"""Pure GBM price-generation engine (`MARKET_SIMULATOR.md` §2–§5).

No I/O, no async, no knowledge of the cache or the tracked-set source — it
only knows about the ticker set it's handed on each call. Safe to construct
once and reuse for the lifetime of the process: state persists between
`step()` calls, which is what makes the walk continuous rather than
re-randomized every tick.
"""

from __future__ import annotations

import math
import random
from typing import AbstractSet

from .simulator_seeds import SECTORS, SeedSpec, spec_for

TICK_INTERVAL_SECONDS = 0.5
TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600

IDIOSYNCRATIC_WEIGHT = 0.6
SECTOR_WEIGHT = 0.8

EVENT_PROBABILITY = 0.002
EVENT_MIN_PCT = 0.02
EVENT_MAX_PCT = 0.05

MIN_PRICE = 0.01  # floor, so a long losing streak can't cross into/through zero


class SimulatorEngine:
    """
    Pure, in-process GBM price generator. No I/O, no async, no knowledge of
    the cache or the tracked-set source — it only knows about the ticker
    set it's handed on each call. Safe to construct once and reuse for the
    lifetime of the process (state persists between `step()` calls, which is
    what makes the walk continuous rather than re-randomized every tick).
    """

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)
        self._specs: dict[str, SeedSpec] = {}
        self._prices: dict[str, float] = {}

    def sync_tracked(self, tickers: AbstractSet[str]) -> None:
        """Seed any newly tracked ticker; forget any ticker no longer tracked."""
        for ticker in tickers:
            if ticker not in self._prices:
                self._seed(ticker)
        for ticker in list(self._prices):
            if ticker not in tickers:
                del self._prices[ticker]
                del self._specs[ticker]

    def step(self, tickers: AbstractSet[str]) -> dict[str, float]:
        """Advance one tick for exactly the given tickers and return their
        new prices. Assumes `sync_tracked(tickers)` has already been called
        this cycle."""
        sector_factors = {s: self._rng.gauss(0, 1) for s in SECTORS}
        sector_events = {s: self._roll_sector_event() for s in SECTORS}
        return {
            ticker: self._step_one(ticker, sector_factors, sector_events) for ticker in tickers
        }

    def _seed(self, ticker: str) -> None:
        spec = spec_for(ticker)
        self._specs[ticker] = spec
        self._prices[ticker] = spec.price

    def _roll_sector_event(self) -> float | None:
        """One shared roll per sector per tick, mirroring `sector_factors`.

        A ticker-independent roll (the original design) fires ~10 times per
        ticker over a 5,000-tick window; since a single 2-5% jump carries
        ~250x the variance of a normal diffusive tick, those independent
        per-ticker jumps dominate tick-to-tick variance and drown out the
        sector-correlated diffusion signal entirely. Sharing one roll across
        every ticker in the sector — same news, same tick — keeps each
        ticker's own event frequency at `EVENT_PROBABILITY` (unchanged from
        `PLAN.md`/`MARKET_SIMULATOR.md` §2.4) while making the jump itself
        move sector-mates together, consistent with §2.3's "tech stocks move
        together" instead of fighting it.
        """
        if self._rng.random() >= EVENT_PROBABILITY:
            return None
        pct = self._rng.uniform(EVENT_MIN_PCT, EVENT_MAX_PCT)
        if self._rng.random() < 0.5:
            pct = -pct
        return pct

    def _step_one(
        self,
        ticker: str,
        sector_factors: dict[str, float],
        sector_events: dict[str, float | None],
    ) -> float:
        spec = self._specs[ticker]
        price = self._prices[ticker]

        dt = TICK_INTERVAL_SECONDS / TRADING_SECONDS_PER_YEAR
        idio = self._rng.gauss(0, 1)
        z = IDIOSYNCRATIC_WEIGHT * idio + SECTOR_WEIGHT * sector_factors[spec.sector]

        drift = (spec.mu - 0.5 * spec.sigma**2) * dt
        diffusion = spec.sigma * (dt**0.5) * z
        new_price = price * math.exp(drift + diffusion)

        event_pct = sector_events[spec.sector]
        if event_pct is not None:
            new_price *= 1 + event_pct

        new_price = max(round(new_price, 2), MIN_PRICE)
        self._prices[ticker] = new_price
        return new_price

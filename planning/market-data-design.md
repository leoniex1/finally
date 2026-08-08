# Market Data Backend — Detailed Design

This document is the implementation contract for the market data subsystem described in
`PLAN.md` §6 (Market Data), §7 (Database — background task supervision), and §8 (Market Data
API endpoints). It is written for the Backend/Market Data agent and is the single source of
truth for anything not spelled out at code level in `PLAN.md`.

Everything here implements, and must not contradict, `PLAN.md`. Where this doc makes a choice
`PLAN.md` left open (e.g., exact Massive endpoint shape, correlation model), it says so.

---

## 1. Scope & Responsibilities

The market data subsystem owns:

1. **The tracked set** — `watchlist ∪ tickers with an open position`, recomputed continuously.
2. **A shared in-memory price cache** — the single source of truth for "what is the price of
   ticker X right now," read by the SSE stream, the history endpoint, the portfolio valuation
   code, and the trade executor.
3. **Two interchangeable data sources** — the simulator (default) and the Massive REST poller
   — both implementing one abstract interface, selected at startup by `MASSIVE_API_KEY`.
4. **The SSE stream** (`GET /api/stream/prices`) and **the history endpoint**
   (`GET /api/prices/{ticker}/history`).
5. **Supervised background execution** — the data source's update loop must survive and log
   exceptions rather than silently dying.

It does **not** own trade execution, portfolio valuation, or watchlist persistence — those are
consumers of the cache (portfolio/trade module) or independent of it (watchlist CRUD, which
only needs to trigger a tracked-set recompute). This doc calls out the integration points but
the trade/portfolio logic itself belongs to a different design doc.

---

## 2. Module Layout

```
backend/
└── app/
    ├── market_data/
    │   ├── __init__.py          # re-exports: PriceCache, CacheEntry, get_market_data_source
    │   ├── models.py            # CacheEntry, PricePoint, TickerStatus, SSEPriceEvent
    │   ├── cache.py             # PriceCache
    │   ├── tracked_set.py       # TrackedSetProvider
    │   ├── source.py            # MarketDataSource ABC
    │   ├── simulator.py         # SimulatorSource
    │   ├── massive.py           # MassiveSource
    │   ├── supervisor.py        # run_supervised()
    │   └── factory.py           # build_market_data_source(settings) -> MarketDataSource
    ├── api/
    │   └── routes/
    │       └── market_data.py   # GET /api/stream/prices, GET /api/prices/{ticker}/history
    ├── db/
    │   └── ...                  # schema, seed (separate design doc), used by tracked_set.py
    ├── config.py                 # Settings (env vars)
    └── main.py                   # FastAPI app + lifespan wiring
```

Nothing in `market_data/` imports from `api/` — the dependency runs one way, `api/` depends on
`market_data/`. `market_data/tracked_set.py` depends on `db/` (it needs to query watchlist and
positions), so `db/` sits below `market_data/`.

---

## 3. Data Model

```python
# app/market_data/models.py
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Deque, Literal, Optional


class TickerStatus(str, Enum):
    PENDING = "pending"          # tracked, no tick has arrived yet
    OK = "ok"                    # has a live price
    UNAVAILABLE = "unavailable"  # source has no data for this ticker


class ReferenceKind(str, Enum):
    PREV_CLOSE = "prev_close"
    SESSION_OPEN = "session_open"


Direction = Literal["up", "down", "flat"]


@dataclass(frozen=True, slots=True)
class PricePoint:
    """One point in a ticker's history ring buffer."""
    t: float          # unix epoch seconds
    price: float


@dataclass(slots=True)
class CacheEntry:
    """Everything the cache knows about one tracked ticker."""
    ticker: str
    price: Optional[float] = None
    prev_price: Optional[float] = None
    reference_price: Optional[float] = None
    reference_kind: Optional[ReferenceKind] = None
    updated_at: float = 0.0          # epoch seconds of the last *change* to `price`
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


HISTORY_MAXLEN = 2000  # ~ an hour of simulator ticks at 500ms cadence, per PLAN.md §6
```

`CacheEntry` is intentionally a plain dataclass, not a Pydantic model — it is mutated in a hot
loop (every ~500ms per tracked ticker) and never crosses a process boundary directly; only
`to_sse_event()` / the history endpoint serialize it. Pydantic response models live in
`api/routes/market_data.py` and are built from these dicts.

---

## 4. `PriceCache`

The cache is a single object, constructed once at startup and shared (via FastAPI's `app.state`)
between the background data-source task, the SSE route, the history route, and — outside this
module's concern — the portfolio valuation code.

```python
# app/market_data/cache.py
from __future__ import annotations

import time
from typing import Iterable, Optional

from .models import CacheEntry, PricePoint, ReferenceKind, TickerStatus


class PriceCache:
    """
    In-memory store of the latest known state for every tracked ticker.

    Concurrency model: this app runs a single asyncio event loop with no threads
    touching the cache. Every method here is synchronous and contains no `await`,
    so each call is atomic with respect to the loop — two coroutines can never
    interleave in the middle of a mutation. This is why no asyncio.Lock is used.
    If the app ever moves to multiple worker processes, the cache must move to
    a shared store (e.g., Redis) — it does not survive process boundaries today.
    """

    def __init__(self) -> None:
        self._entries: dict[str, CacheEntry] = {}

    def ensure_tracked(self, ticker: str) -> CacheEntry:
        """Idempotently register a ticker as tracked, defaulting to PENDING."""
        entry = self._entries.get(ticker)
        if entry is None:
            entry = CacheEntry(ticker=ticker)
            self._entries[ticker] = entry
        return entry

    def drop_untracked(self, still_tracked: set[str]) -> None:
        """Remove cache entries for tickers no longer in the tracked set."""
        for ticker in list(self._entries):
            if ticker not in still_tracked:
                del self._entries[ticker]

    def update(
        self,
        ticker: str,
        price: float,
        *,
        reference_price: Optional[float] = None,
        reference_kind: Optional[ReferenceKind] = None,
        now: Optional[float] = None,
    ) -> None:
        """
        Record a new price tick for a tracked ticker. Called by the active
        data source (simulator or Massive poller) once per observed price.

        If `reference_price` is omitted, the entry keeps its existing reference
        (or, on first tick, adopts `price` itself as a session-open reference —
        see `_seed_reference`).
        """
        now = now if now is not None else time.time()
        entry = self.ensure_tracked(ticker)

        entry.prev_price = entry.price if entry.price is not None else price
        entry.price = price
        entry.status = TickerStatus.OK
        entry.updated_at = now
        entry.history.append(PricePoint(t=now, price=price))

        if reference_price is not None:
            entry.reference_price = reference_price
            entry.reference_kind = reference_kind or ReferenceKind.PREV_CLOSE
        elif entry.reference_price is None:
            self._seed_reference(entry, price)

    def _seed_reference(self, entry: CacheEntry, price: float) -> None:
        entry.reference_price = price
        entry.reference_kind = ReferenceKind.SESSION_OPEN

    def mark_unavailable(self, ticker: str) -> None:
        """Source polled but returned no data for this ticker (still tracked)."""
        entry = self.ensure_tracked(ticker)
        entry.status = TickerStatus.UNAVAILABLE

    def get(self, ticker: str) -> Optional[CacheEntry]:
        return self._entries.get(ticker)

    def snapshot(self) -> list[CacheEntry]:
        """A point-in-time list of all tracked entries, for SSE initial connect."""
        return list(self._entries.values())

    def tracked_tickers(self) -> set[str]:
        return set(self._entries.keys())
```

Notes:

- `update()` is the **only** place `updated_at` changes, and it only changes on a price change
  — this is what makes the SSE diffing in §9 correct: "emit only when `updated_at` has advanced."
- `mark_unavailable` does not touch `price`/`updated_at`. A ticker that was `ok` and then goes
  quiet on a Massive response keeps its last known price (frozen) but flips `status`, so the
  frontend renders `—` per §6/§10 rather than a stale-but-labeled-live number.
- `drop_untracked` is called once per poll/sim cycle (§5) after recomputing the tracked set —
  this is what makes "watchlist changes recompute the tracked set" concrete: a ticker that is
  no longer watched *and* has no position simply stops appearing in the next cycle's snapshot.

---

## 5. Tracked Set

```python
# app/market_data/tracked_set.py
from __future__ import annotations

import aiosqlite


class TrackedSetProvider:
    """
    Computes tracked = watchlist tickers ∪ tickers with an open position.

    Queried fresh on every data-source cycle (every ~500ms for the simulator,
    every poll interval for Massive) rather than pushed via events. Both
    tables are tiny (tens of rows), so a fresh SELECT per cycle is cheap and
    avoids a second consistency mechanism (pub/sub, invalidation) for what is
    already a polling loop.
    """

    def __init__(self, db_path: str, user_id: str = "default") -> None:
        self._db_path = db_path
        self._user_id = user_id

    async def get(self) -> set[str]:
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                """
                SELECT ticker FROM watchlist WHERE user_id = ?
                UNION
                SELECT ticker FROM positions WHERE user_id = ? AND quantity > 1e-6
                """,
                (self._user_id, self._user_id),
            )
            rows = await cursor.fetchall()
        return {row[0] for row in rows}
```

Why polling the DB instead of an in-process event bus: watchlist `POST`/`DELETE` and trade
execution already write to SQLite; re-deriving the tracked set from those tables each cycle
means there is exactly one source of truth (the DB) and zero risk of the in-memory tracked set
drifting from it after a crash/restart mid-update. The cost is one cheap `UNION` query every
500ms, which SQLite handles trivially at this scale.

This satisfies the two concrete requirements in `PLAN.md` §6:
- A newly-added ticker joins the tracked set on the *next* cycle (≤500ms for the simulator).
- A ticker removed from the watchlist but still held stays tracked, because the `UNION` still
  picks it up from `positions`.

---

## 6. `MarketDataSource` Abstract Interface

```python
# app/market_data/source.py
from __future__ import annotations

from abc import ABC, abstractmethod

from .cache import PriceCache
from .tracked_set import TrackedSetProvider


class MarketDataSource(ABC):
    """
    One implementation runs for the lifetime of the process (simulator or
    Massive). `run()` never returns under normal operation — it is wrapped
    by `run_supervised()` (see supervisor.py), which restarts it with
    backoff if it raises.
    """

    def __init__(self, cache: PriceCache, tracked_set: TrackedSetProvider) -> None:
        self.cache = cache
        self.tracked_set = tracked_set

    @abstractmethod
    async def run(self) -> None:
        """Run forever: on each cycle, recompute the tracked set, fetch/generate
        prices for it, and write them into `self.cache`."""
        raise NotImplementedError
```

Both `SimulatorSource` and `MassiveSource` subclass this and share the shape:

```python
async def run(self) -> None:
    while True:
        tracked = await self.tracked_set.get()
        self.cache.drop_untracked(tracked)
        for ticker in tracked:
            self.cache.ensure_tracked(ticker)   # keeps `pending` visible immediately
        await self._update_prices(tracked)
        await asyncio.sleep(self._interval_seconds)
```

`_update_prices` is where the two implementations diverge.

---

## 7. Simulator (`SimulatorSource`)

### 7.1 Requirements recap (PLAN.md §6)

- GBM per ticker, configurable drift/volatility.
- ~500ms update interval.
- Correlated moves across related tickers (e.g. tech names move together).
- Occasional random "events": sudden 2–5% moves.
- Known tickers start from realistic seed prices; unknown tickers get a deterministic
  hash-derived seed in ~$20–$400, default drift/volatility.
- Newly tracked tickers start ticking on the next cycle.

### 7.2 Seed table and sector grouping

```python
# app/market_data/simulator.py
from __future__ import annotations

import asyncio
import hashlib
import random
import time
from dataclasses import dataclass

from .cache import PriceCache
from .models import ReferenceKind
from .source import MarketDataSource
from .tracked_set import TrackedSetProvider

TICK_INTERVAL_SECONDS = 0.5

# Annual drift (mu) and volatility (sigma) — converted to per-tick values in _step().
DEFAULT_MU = 0.05
DEFAULT_SIGMA = 0.35

@dataclass(frozen=True)
class SeedSpec:
    price: float
    sector: str
    mu: float = DEFAULT_MU
    sigma: float = DEFAULT_SIGMA

KNOWN_TICKERS: dict[str, SeedSpec] = {
    "AAPL":  SeedSpec(190.0, "tech"),
    "GOOGL": SeedSpec(175.0, "tech"),
    "MSFT":  SeedSpec(420.0, "tech"),
    "AMZN":  SeedSpec(185.0, "tech"),
    "TSLA":  SeedSpec(250.0, "auto",  sigma=0.55),
    "NVDA":  SeedSpec(120.0, "tech",  sigma=0.5),
    "META":  SeedSpec(500.0, "tech"),
    "JPM":   SeedSpec(210.0, "finance", sigma=0.25),
    "V":     SeedSpec(275.0, "finance", sigma=0.2),
    "NFLX":  SeedSpec(650.0, "media", sigma=0.4),
}

SECTORS = ("tech", "finance", "auto", "media", "other")


def seed_for_unknown_ticker(ticker: str) -> SeedSpec:
    """Deterministic seed price in [$20, $400] derived from a hash of the symbol,
    so a given symbol always starts at the same price across restarts."""
    digest = hashlib.sha256(ticker.encode("utf-8")).digest()
    # Use the first 4 bytes as an unsigned int for a stable, well-distributed value.
    n = int.from_bytes(digest[:4], "big")
    price = 20.0 + (n % 38000) / 100.0   # 20.00 .. 399.99
    sector = SECTORS[digest[4] % len(SECTORS)]
    return SeedSpec(price=price, sector=sector)


def spec_for(ticker: str) -> SeedSpec:
    return KNOWN_TICKERS.get(ticker) or seed_for_unknown_ticker(ticker)
```

### 7.3 GBM step with sector correlation and event jumps

Standard discrete GBM: `S_{t+dt} = S_t * exp((mu - sigma^2/2) dt + sigma * sqrt(dt) * Z)`.
Correlation across tickers in the same sector is introduced by splitting each ticker's random
shock `Z` into a shared sector factor and an idiosyncratic factor:

```python
IDIOSYNCRATIC_WEIGHT = 0.6   # per-ticker noise
SECTOR_WEIGHT = 0.8          # shared sector factor (weights need not sum to 1; they
                              # scale independent unit-variance draws combined below)
EVENT_PROBABILITY = 0.002    # ~ once every ~1000 ticks (~8 min) per ticker at 500ms
EVENT_MIN_PCT = 0.02
EVENT_MAX_PCT = 0.05


class SimulatorSource(MarketDataSource):
    def __init__(self, cache: PriceCache, tracked_set: TrackedSetProvider) -> None:
        super().__init__(cache, tracked_set)
        self._interval_seconds = TICK_INTERVAL_SECONDS
        self._rng = random.Random()
        self._prices: dict[str, float] = {}     # last simulated price, keyed by ticker
        self._specs: dict[str, SeedSpec] = {}

    async def run(self) -> None:
        while True:
            tracked = await self.tracked_set.get()
            self.cache.drop_untracked(tracked)
            self._forget_untracked(tracked)

            for ticker in tracked:
                self.cache.ensure_tracked(ticker)
                if ticker not in self._prices:
                    self._seed(ticker)

            sector_factors = {s: self._rng.gauss(0, 1) for s in SECTORS}
            now = time.time()
            for ticker in tracked:
                self._step(ticker, sector_factors, now)

            await asyncio.sleep(self._interval_seconds)

    def _forget_untracked(self, tracked: set[str]) -> None:
        for ticker in list(self._prices):
            if ticker not in tracked:
                del self._prices[ticker]
                self._specs.pop(ticker, None)

    def _seed(self, ticker: str) -> None:
        spec = spec_for(ticker)
        self._specs[ticker] = spec
        self._prices[ticker] = spec.price
        # First observation becomes the session-open reference (PLAN.md §6);
        # `update()` seeds this automatically when reference_price is omitted,
        # but we pass the price explicitly here so the *first* SSE event and
        # the seed price agree exactly.
        self.cache.update(ticker, spec.price, now=time.time())

    def _step(self, ticker: str, sector_factors: dict[str, float], now: float) -> None:
        spec = self._specs[ticker]
        price = self._prices[ticker]

        dt = self._interval_seconds / (252 * 6.5 * 3600)  # fraction of a trading year
        idio = self._rng.gauss(0, 1)
        z = IDIOSYNCRATIC_WEIGHT * idio + SECTOR_WEIGHT * sector_factors[spec.sector]

        drift = (spec.mu - 0.5 * spec.sigma ** 2) * dt
        diffusion = spec.sigma * (dt ** 0.5) * z
        new_price = price * pow(2.718281828, drift + diffusion)

        if self._rng.random() < EVENT_PROBABILITY:
            pct = self._rng.uniform(EVENT_MIN_PCT, EVENT_MAX_PCT)
            if self._rng.random() < 0.5:
                pct = -pct
            new_price *= (1 + pct)

        new_price = max(new_price, 0.01)
        self._prices[ticker] = new_price
        self.cache.update(ticker, round(new_price, 2), now=now)
```

Design notes:

- `sector_factors` is drawn **once per cycle**, shared by every ticker in that sector that
  cycle — that shared draw is exactly what produces correlated moves ("tech stocks move
  together") without hardcoding pairwise correlations.
- `EVENT_PROBABILITY` is evaluated per ticker per tick; at 500ms and ~0.2%, a given ticker gets
  a dramatic 2–5% jump roughly every 8 minutes — frequent enough to be "drama" in a demo without
  making the tape unreadable.
- `cache.update()` is called every cycle for every tracked ticker (not only when the price
  "meaningfully" changes) — GBM almost never produces an exact repeat, so this is fine, and it
  keeps the simulator's contract simple: one `update()` call = one real price change =
  `updated_at` advances = SSE emits it downstream. If a future tuning pass makes ticks that
  round to the same 2-decimal price common, skip the `update()` call in that case so
  `updated_at` (and therefore SSE traffic) only reflects real change — the diffing logic in §9
  already assumes "same price → no event," this would just make it true at the source too.

---

## 8. Massive API Client (`MassiveSource`)

### 8.1 Requirements recap (PLAN.md §6)

- REST polling, not WebSocket.
- Interval by tier: 15s free / 2–15s paid, configurable.
- Parses into the same cache shape as the simulator.
- Missing data for a tracked ticker → `status: "unavailable"`, not a fabricated price; retried
  every subsequent poll.
- Prefer the API's previous-close when present → `reference_kind = "prev_close"`.

### 8.2 Assumptions to confirm against Massive's live docs

`PLAN.md` names Massive as a "Massive (Polygon.io) API" without pinning exact endpoint paths.
This design assumes a Polygon.io-shaped REST surface (previous-close + snapshot/last-trade
endpoints) behind the `MASSIVE_API_KEY`. The parsing boundary below (`_parse_response`) is the
single place that would need to change if the real endpoint shape differs — nothing else in the
system depends on Massive's wire format.

```python
# app/market_data/massive.py
from __future__ import annotations

import asyncio
import time
from typing import Optional

import httpx

from .cache import PriceCache
from .models import ReferenceKind
from .source import MarketDataSource
from .tracked_set import TrackedSetProvider

MASSIVE_BASE_URL = "https://api.massive.example/v2"  # confirm against live Massive docs
REQUEST_TIMEOUT_SECONDS = 8.0


class MassiveSource(MarketDataSource):
    def __init__(
        self,
        cache: PriceCache,
        tracked_set: TrackedSetProvider,
        api_key: str,
        poll_interval_seconds: float = 15.0,
    ) -> None:
        super().__init__(cache, tracked_set)
        self._api_key = api_key
        self._interval_seconds = poll_interval_seconds
        self._client = httpx.AsyncClient(
            base_url=MASSIVE_BASE_URL,
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def run(self) -> None:
        try:
            while True:
                tracked = await self.tracked_set.get()
                self.cache.drop_untracked(tracked)
                for ticker in tracked:
                    self.cache.ensure_tracked(ticker)

                if tracked:
                    await self._poll(tracked)

                await asyncio.sleep(self._interval_seconds)
        finally:
            await self._client.aclose()

    async def _poll(self, tracked: set[str]) -> None:
        try:
            response = await self._client.get(
                "/snapshot/tickers",
                params={"tickers": ",".join(sorted(tracked))},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # Network/parse failure for the whole poll: leave every ticker's
            # existing cache state untouched (do NOT mark unavailable — a
            # transient network blip is not the same as "the API confirmed
            # no data for this symbol"). Log and retry next cycle.
            _log_poll_failure(exc)
            return

        seen = self._parse_response(payload, now=time.time())
        for ticker in tracked - seen:
            self.cache.mark_unavailable(ticker)

    def _parse_response(self, payload: dict, now: float) -> set[str]:
        """Update the cache for every ticker present in the response; return
        the set of tickers actually seen so the caller can mark the rest
        `unavailable`."""
        seen: set[str] = set()
        for row in payload.get("tickers", []):
            ticker = row.get("ticker")
            last_price = _extract_last_price(row)
            if not ticker or last_price is None:
                continue

            prev_close = _extract_prev_close(row)
            self.cache.update(
                ticker,
                last_price,
                reference_price=prev_close,
                reference_kind=ReferenceKind.PREV_CLOSE if prev_close else None,
                now=now,
            )
            seen.add(ticker)
        return seen


def _extract_last_price(row: dict) -> Optional[float]:
    last_trade = row.get("lastTrade") or {}
    price = last_trade.get("p")
    return float(price) if price is not None else None


def _extract_prev_close(row: dict) -> Optional[float]:
    prev_day = row.get("prevDay") or {}
    close = prev_day.get("c")
    return float(close) if close is not None else None


def _log_poll_failure(exc: Exception) -> None:
    import logging
    logging.getLogger("market_data.massive").warning("Massive poll failed: %s", exc)
```

Key correctness points, tied directly to `PLAN.md` §6:

- **A ticker missing from `payload["tickers"]` is marked `unavailable`, never assigned a stale
  or zero price.** This is the `seen = ...; tracked - seen → mark_unavailable` loop.
- **A whole-request failure (network error, non-2xx, bad JSON) does not mark anything
  unavailable** — that would incorrectly flip every tracked ticker to unavailable on a single
  transient blip. Only an ticker *absent from a successful response* is unavailable; a failed
  request just retries next cycle with prior cache state intact.
- **`reference_price`/`reference_kind` come from `prevDay.c` when present**, else `update()`'s
  default (`_seed_reference`) kicks in and falls back to session-open on that ticker's first
  successful tick — matching "Otherwise (always, for the simulator) the reference is the first
  price this process observed."
- Tier-driven poll interval is a plain constructor parameter, set from `Settings` (§10) — e.g.
  `MASSIVE_POLL_INTERVAL_SECONDS` env var, default `15.0`.

---

## 9. Background Task Supervision

```python
# app/market_data/supervisor.py
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

logger = logging.getLogger("market_data.supervisor")

INITIAL_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 30.0


async def run_supervised(name: str, coro_factory: Callable[[], Awaitable[None]]) -> None:
    """
    Runs `coro_factory()` forever. If it raises, logs the exception and
    restarts it after an exponential backoff (capped), so one bad tick
    (e.g. a parsing bug, a transient exception) degrades to "prices stop
    updating for a few seconds" rather than killing the app's only price
    feed for the rest of the process lifetime.
    """
    backoff = INITIAL_BACKOFF_SECONDS
    while True:
        try:
            await coro_factory()
            # A well-behaved source's `run()` never returns; treat a clean
            # return as a bug too, and restart it rather than leaving the
            # cache frozen forever.
            logger.error("%s.run() returned unexpectedly; restarting", name)
        except asyncio.CancelledError:
            raise  # let shutdown propagate; do not swallow cancellation
        except Exception:
            logger.exception("%s crashed; restarting in %.1fs", name, backoff)

        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
```

This is the same supervision primitive used for the portfolio-snapshot task (§7 of `PLAN.md`
requires the same guarantee there — one `run_supervised()` wraps both background tasks).

---

## 10. Configuration & Source Selection

```python
# app/config.py (market-data-relevant slice)
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    massive_api_key: str = ""
    massive_poll_interval_seconds: float = 15.0
    db_path: str = "db/finally.db"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            massive_api_key=os.environ.get("MASSIVE_API_KEY", "").strip(),
            massive_poll_interval_seconds=float(
                os.environ.get("MASSIVE_POLL_INTERVAL_SECONDS", "15.0")
            ),
            db_path=os.environ.get("DB_PATH", "db/finally.db"),
        )

    @property
    def use_massive(self) -> bool:
        return bool(self.massive_api_key)
```

```python
# app/market_data/factory.py
from __future__ import annotations

from ..config import Settings
from .cache import PriceCache
from .massive import MassiveSource
from .simulator import SimulatorSource
from .source import MarketDataSource
from .tracked_set import TrackedSetProvider


def build_market_data_source(
    settings: Settings, cache: PriceCache, tracked_set: TrackedSetProvider
) -> MarketDataSource:
    if settings.use_massive:
        return MassiveSource(
            cache,
            tracked_set,
            api_key=settings.massive_api_key,
            poll_interval_seconds=settings.massive_poll_interval_seconds,
        )
    return SimulatorSource(cache, tracked_set)
```

This is the entire selection logic `PLAN.md` §5 describes: presence of a non-empty
`MASSIVE_API_KEY` is the only switch, checked once at startup. Nothing downstream (cache
consumers, SSE route, history route) knows or cares which implementation is active.

---

## 11. FastAPI Lifespan Wiring

Ordering matters (`PLAN.md` §7): schema/seed must exist *before* any background task starts.

```python
# app/main.py
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import Settings
from .db.init import init_db                      # separate design doc; must run first
from .market_data.cache import PriceCache
from .market_data.factory import build_market_data_source
from .market_data.supervisor import run_supervised
from .market_data.tracked_set import TrackedSetProvider


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    await init_db(settings.db_path)  # create schema + seed if needed, per PLAN.md §7

    cache = PriceCache()
    tracked_set = TrackedSetProvider(settings.db_path)
    source = build_market_data_source(settings, cache, tracked_set)

    app.state.settings = settings
    app.state.price_cache = cache

    tasks = [
        asyncio.create_task(run_supervised("market_data_source", source.run)),
        # asyncio.create_task(run_supervised("portfolio_snapshot", snapshot_task.run)),
        # ^ owned by the portfolio module's design doc; wired the same way here.
    ]

    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(lifespan=lifespan)
```

`app.state.price_cache` is how the SSE route, history route, and (in the portfolio module)
trade execution and `/api/portfolio` all reach the same cache instance — FastAPI request
handlers access it via `request.app.state.price_cache`.

---

## 12. SSE Streaming Endpoint

### 12.1 Requirements recap (PLAN.md §6)

- `GET /api/stream/prices`.
- On connect: one event per tracked ticker, full current state — no waiting for a tick.
- Thereafter: emit a ticker's event **only when its `updated_at` has advanced** since the last
  event sent *on this connection*. Checked at ~500ms; unchanged tickers produce no traffic.
- `: ping` heartbeat comment every 15s.
- `EventSource` on the client handles reconnection; every reconnect gets a fresh full snapshot.

### 12.2 Implementation

```python
# app/api/routes/market_data.py
from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

router = APIRouter()

STREAM_CHECK_INTERVAL_SECONDS = 0.5
HEARTBEAT_INTERVAL_SECONDS = 15.0


@router.get("/api/stream/prices")
async def stream_prices(request: Request) -> StreamingResponse:
    cache = request.app.state.price_cache

    async def event_source():
        last_sent_at: dict[str, float] = {}

        # Initial full snapshot — every tracked ticker, regardless of updated_at.
        for entry in cache.snapshot():
            yield _format_event(entry.to_sse_event())
            last_sent_at[entry.ticker] = entry.updated_at

        last_heartbeat = time.monotonic()
        while True:
            if await request.is_disconnected():
                break

            for entry in cache.snapshot():
                sent_at = last_sent_at.get(entry.ticker)
                if sent_at is None or entry.updated_at > sent_at:
                    yield _format_event(entry.to_sse_event())
                    last_sent_at[entry.ticker] = entry.updated_at

            # Drop bookkeeping for tickers that left the tracked set, so a
            # ticker re-tracked later is treated as "never sent" again.
            for ticker in list(last_sent_at):
                if cache.get(ticker) is None:
                    del last_sent_at[ticker]

            now = time.monotonic()
            if now - last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
                yield ": ping\n\n"
                last_heartbeat = now

            await asyncio.sleep(STREAM_CHECK_INTERVAL_SECONDS)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable nginx buffering if fronted by one
        },
    )


def _format_event(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"
```

Why this satisfies the Massive-cadence requirement precisely: `last_sent_at` is scoped **per
connection** (a local variable inside `event_source()`), not global. At a 15-second Massive poll,
`entry.updated_at` only advances once every 15s (because `cache.update()` — and therefore the
`updated_at` write — only happens when `MassiveSource._parse_response` actually processes a new
poll result), so the `entry.updated_at > sent_at` check is false for ~29 of the 30 checks between
polls, and the loop emits nothing. The 500ms poll loop here is just the *check* cadence, not the
*emit* cadence — this is the distinction `PLAN.md` draws out explicitly.

`request.is_disconnected()` plus letting the generator end naturally is how cleanup happens;
there's no explicit unsubscribe step because nothing is subscribed — every connection just reads
`cache.snapshot()` independently on its own loop.

---

## 13. History Endpoint

```python
@router.get("/api/prices/{ticker}/history")
async def get_price_history(ticker: str, request: Request):
    cache = request.app.state.price_cache
    entry = cache.get(ticker.upper())
    if entry is None:
        raise HTTPException(status_code=404, detail=f"{ticker} is not tracked")

    return {
        "ticker": entry.ticker,
        "reference_price": entry.reference_price,
        "reference_kind": entry.reference_kind.value if entry.reference_kind else None,
        "points": [{"t": p.t, "price": p.price} for p in entry.history],
    }
```

- `404` for anything not currently tracked, per `PLAN.md` §8 — this is a plain `cache.get()`
  miss, since untracked tickers are pruned from the cache every cycle (§4 `drop_untracked`).
  Note this means a ticker can 404 in a narrow race right after being dropped from the
  watchlist and before a position exists — that is correct per the tracked-set definition, not
  a bug: an untracked ticker has no price to backfill a chart with.
  On a fresh container, `entry.history` may be empty or short (in-memory, lost on restart) —
  the endpoint still returns `200` with `points: []`, and the frontend fills in from SSE as
  documented in §6/§10 of `PLAN.md`. No special-casing needed; an empty deque serializes to `[]`
  naturally.

---

## 14. Integration Points for Other Modules

These are the exact calls other parts of the backend make into this module — listed here so the
portfolio/trade and DB design docs can code against a stable surface:

| Consumer | Call | Behavior |
|---|---|---|
| Trade executor (`POST /api/portfolio/trade`) | `cache.get(ticker)` | `None` or `status != "ok"` → `409` per `PLAN.md` §8 trade rules table. Never trade off `mark_unavailable`'d or absent entries. |
| Portfolio valuation (`GET /api/portfolio`) | `cache.get(ticker)` per open position | `status == "unavailable"` → value the position at `quantity × avg_cost` and set `priced: false` on that position in the response. |
| Watchlist `POST`/`DELETE` | *(none directly)* | Just writes to the `watchlist` table. `TrackedSetProvider.get()` picks up the change on the source's next cycle — no explicit notification needed, per the polling design in §5. |
| App startup | `init_db()` → then `build_market_data_source()` → then `run_supervised(...)` | Strict order, per §11 and `PLAN.md` §7. |

---

## 15. Testing Hooks

Everything above is written to make the unit tests enumerated in `PLAN.md` §12 straightforward
without extra scaffolding:

- **GBM correctness / seed determinism**: `spec_for("ZZZZ")` is a pure function — call it twice,
  assert equal. `seed_for_unknown_ticker` output stays inside `[20.0, 400.0)` by construction
  (`20.0 + (n % 38000) / 100.0`).
- **Both sources conform to the interface**: a single parametrized test suite instantiates both
  `SimulatorSource(cache, tracked_set)` and `MassiveSource(cache, tracked_set, api_key="x")`
  against a `TrackedSetProvider` stub (or a fake with a `.get()` coroutine) and asserts both
  expose `.run()` and both, given a controlled tracked set, populate `cache.get(ticker).status`.
- **Tracked set survives watchlist removal**: seed `positions` with a row, omit it from
  `watchlist`, call `TrackedSetProvider.get()`, assert the ticker is present.
- **SSE unchanged tickers emit nothing**: construct a `PriceCache`, call `cache.update()` once,
  drive `event_source()` for a few checker-loop iterations without another `update()` call, and
  assert only the initial snapshot event was yielded — this directly tests the
  `entry.updated_at > sent_at` gate in §12.2 without needing a real Massive poll cadence.
  (Since `event_source()` is an async generator taking a live `PriceCache`, it can be driven in
  a test by calling `.__anext__()` manually rather than running the full `StreamingResponse`.)
- **New connection gets a full snapshot**: assert the first `HISTORY_MAXLEN`-independent branch
  in `event_source()` yields one event per `cache.snapshot()` entry regardless of `updated_at`.
- **Massive unavailable-ticker handling**: feed `_parse_response` a payload missing one tracked
  ticker; assert `cache.get(that_ticker).status == "unavailable"` and its `price` is unchanged
  from before the poll.
- **Massive reference kind**: feed a response with `prevDay.c` present vs. absent; assert
  `reference_kind` is `prev_close` vs. `session_open` respectively.
- **History endpoint bounds**: seed `entry.history` past `HISTORY_MAXLEN` via repeated
  `cache.update()` calls; assert `len(entry.history) == HISTORY_MAXLEN` (the `deque(maxlen=...)`
  enforces this structurally, so this test is really confirming the maxlen wiring, not writing
  eviction logic by hand).
- **Supervisor restarts on exception**: pass `run_supervised` a `coro_factory` that raises once
  then succeeds; assert it's called twice and the second call's success ends the test (use a
  short `MAX_BACKOFF_SECONDS` override or monkeypatch `asyncio.sleep` in the test).

---

## 16. Summary Checklist Against `PLAN.md`

- [x] Tracked set = watchlist ∪ open positions, recomputed every cycle (§5).
- [x] Simulator: GBM, ~500ms, sector-correlated moves, random event jumps, known-ticker seed
      table, deterministic hash-seeded unknown tickers (§7).
- [x] Massive: REST polling (not WebSocket), configurable interval, `unavailable` (not
      fabricated) on missing data, retried every poll (§8).
- [x] Shared cache fields exactly as specified: `price`, `prev_price`, `reference_price`,
      `reference_kind`, `updated_at`, `status`, `history` (§3, §4).
- [x] `reference_price`/`reference_kind` semantics: Massive prev-close when present, else
      first-observed session-open, for both sources (§4, §8).
- [x] `history` capped ~2000 points, in-memory only, appended only on real change (§4).
- [x] SSE: full snapshot on connect, diffed thereafter by `updated_at`, 15s heartbeat,
      reconnect-safe by construction (§12).
- [x] `GET /api/prices/{ticker}/history`: 404 for untracked, else reference + points (§13).
- [x] Background task supervision with exponential backoff, logged, never dies silently (§9).
- [x] Startup ordering: schema/seed → cache/source construction → supervised tasks → serve (§11).
- [x] Source selection is a pure function of `MASSIVE_API_KEY` presence, checked once (§10).

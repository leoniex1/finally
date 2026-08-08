# Market Simulator — Detailed Design

## Relationship to the other planning documents

This document covers **only** the price-generation math and the `SimulatorEngine` class that
implements it. It does not cover:

- How the simulator plugs into the rest of the app as a `MarketDataProvider` — see
  `MARKET_INTERFACE.md` §4.1 (`SimulatorProvider`, a thin wrapper around the engine described
  here).
- The shared price cache, tracked-set computation, SSE streaming, or REST endpoints — see
  `market-data-design.md`.
- Massive's real API, for comparison — see `MASSIVE_API.md`.

The simulator is the **default** data source (`PLAN.md` §5: active whenever `MASSIVE_API_KEY` is
unset), so in practice it's what every student runs out of the box, and its behavior is what
makes the first-launch demo (`PLAN.md` §2) feel alive: ticking prices, occasional excitement,
plausible-looking correlated sector moves.

---

## 1. Requirements Recap (`PLAN.md` §6)

- Prices generated via **geometric Brownian motion (GBM)** with configurable drift/volatility
  per ticker.
- Updates at **~500ms** intervals — cadence is owned by the driver loop in `MARKET_INTERFACE.md`
  §5, not by this engine; the engine just needs `step()` to be cheap enough to call twice a
  second indefinitely.
- **Correlated moves** across related tickers (e.g., tech names move together).
- **Occasional random "events"** — sudden 2–5% moves on a ticker, for drama.
- **Known tickers** start from realistic seed prices (AAPL ~$190, GOOGL ~$175, etc.).
- **Unknown tickers** get a deterministic seed price in ~$20–$400, derived from a hash of the
  symbol — same symbol always starts at the same price, across restarts, with no persisted state.
- A newly tracked ticker starts ticking on its **next cycle** — i.e. the engine must be able to
  seed and price a ticker it has never seen before, on demand, mid-run.
- Runs in-process, no external dependencies (no network, no DB — `TrackedSetProvider` is the only
  outside input, and even that arrives as a plain `set[str]` argument, not a live connection).

---

## 2. The Model

### 2.1 Why GBM

Geometric Brownian motion is the standard toy model for a stock price because it has two
properties that matter for a demo, not just for math purity:

1. **Prices stay positive.** GBM models the log of the price as a random walk, so the price
   itself is `S₀ · exp(...)` — it can approach zero but never cross it, unlike an additive random
   walk on the raw price.
2. **Percentage moves are stationary.** A $5 move means something different on a $20 stock than
   a $600 one; GBM's volatility parameter is a *percentage* volatility, so a `sigma=0.35` ticker
   behaves the same (in relative terms) whether it's seeded at $20 or $400.

### 2.2 The discrete-time update

The continuous-time GBM SDE `dS = μS dt + σS dW` has an exact discretization (not just an Euler
approximation) using the log-normal solution:

```
S(t + dt) = S(t) · exp( (μ − σ²/2)·dt + σ·√dt·Z )       where Z ~ N(0, 1)
```

- `μ` (`mu`) — annualized drift (expected return). `0.05` (5%/year) is a reasonable default —
  enough to give the demo a very slight long-run upward bias without dominating the noise term
  over a session that only lasts minutes.
- `σ` (`sigma`) — annualized volatility. Real large-cap equities run roughly `0.15`–`0.6`
  annualized; the defaults below (§3) spread tickers across that range so some feel "calmer"
  than others.
- `dt` — the fraction of a **trading year** elapsed in one simulator tick. At a 500ms tick and a
  standard `252 trading days × 6.5 trading hours/day` convention:

  ```python
  TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600   # 5,896,800
  dt = TICK_INTERVAL_SECONDS / TRADING_SECONDS_PER_YEAR   # 0.5 / 5,896,800 ≈ 8.48e-8
  ```

  This is deliberately *not* wall-clock time scaled to a real year (that would make individual
  ticks imperceptibly small) — it's calibrated so that `sigma`'s real-world meaning ("this stock's
  annualized volatility is 35%") produces per-tick moves that are visually similar in size to a
  real intraday tape, compressed into a demo session. It is a presentation choice, not a
  forecasting one — nothing about this simulator is meant to be a realistic backtest engine.

### 2.3 Correlated moves across tickers

A literal per-pair correlation matrix doesn't scale to arbitrary tracked tickers (including
unknown symbols added by the user or the LLM at runtime) and isn't necessary for the visual
effect `PLAN.md` asks for ("tech stocks move together"). Instead, each ticker's per-tick shock is
a weighted sum of two independent standard-normal draws:

```
Z_ticker = w_idio · Z_idio  +  w_sector · Z_sector[sector(ticker)]
```

- `Z_idio` — a fresh draw, unique to this ticker, this tick.
- `Z_sector[s]` — one draw **per sector, per tick**, shared by every tracked ticker in that
  sector. This is the entire correlation mechanism: two tech tickers both add the *same* draw
  `Z_sector["tech"]` into their move this tick, so they tend to move together, while an unrelated
  finance ticker adds a different sector draw.

Because `Z_idio` and `Z_sector[s]` are independent standard normals, `Z_ticker` is itself normal
with variance `w_idio² + w_sector²` — not unit variance — so the effective volatility a ticker
experiences is `sigma · sqrt(w_idio² + w_sector²)`, not `sigma` alone. The default weights
(`w_idio=0.6`, `w_sector=0.8`) push the balance toward sector co-movement (`0.8` > `0.6`) while
keeping meaningful idiosyncratic noise — this is a tuning choice (§7), not a derived constant.

### 2.4 Event jumps

Independently of the GBM step, each **sector** has a small per-tick probability of an extra
multiplicative jump, layered on top of that tick's diffusive move, shared by every currently
tracked ticker in that sector — the same mechanism §2.3 uses for `Z_sector`, applied to jumps
instead of diffusion:

```
if random() < EVENT_PROBABILITY:            # rolled once per sector, per tick
    pct ~ Uniform(EVENT_MIN_PCT, EVENT_MAX_PCT), sign chosen 50/50
    for ticker in tickers_in(sector):
        price[ticker] *= (1 + pct)
```

Because the roll is per-sector rather than per-ticker, a single ticker still sees a jump at the
same `EVENT_PROBABILITY` rate as before (it belongs to exactly one sector) — so "roughly every 4
minutes" (below) is unchanged from a given ticker's point of view. What changes is that sector-mates
now jump *together*, on the same tick, by the same percentage — modeling sector-wide news (a rate
decision moving every bank stock, an export ban moving every chip stock) rather than pure
company-specific noise.

This isn't cosmetic: a 2–5% jump is roughly 200–500x larger than a single diffusive tick at this
`dt` (§2.2), so whenever an event fires it dominates that ticker's tick-to-tick variance. If jumps
were rolled independently per ticker, those large, uncorrelated jumps would swamp the much smaller
correlated diffusion signal from §2.3 in any tick-level measurement (e.g. sample correlation of
returns over thousands of ticks) — the sector co-movement the whole mechanism exists to produce
would be statistically undetectable despite being implemented correctly. Sharing the roll per
sector fixes this at the source: the dominant-variance component now carries the correlation
signal instead of erasing it.

At `EVENT_PROBABILITY = 0.002` and a 500ms tick, a given ticker gets a 2–5% jump roughly every
`1 / 0.002 = 500` ticks ≈ **4 minutes**, which is frequent enough that a demo running for a few
minutes will visibly show at least one on most tickers, without turning the whole tape into
constant noise.

---

## 3. Seed Data

### 3.1 Known tickers

`PLAN.md` §7 seeds the default watchlist with AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA, META, JPM, V,
NFLX. Each gets a realistic starting price, a sector (for correlation, §2.3), and optionally a
volatility override for names that are known to be more/less volatile than the sector default:

```python
# app/market_data/simulator_seeds.py
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
    "AAPL":  SeedSpec(190.0, "tech"),
    "GOOGL": SeedSpec(175.0, "tech"),
    "MSFT":  SeedSpec(420.0, "tech"),
    "AMZN":  SeedSpec(185.0, "tech"),
    "TSLA":  SeedSpec(250.0, "auto",   sigma=0.55),
    "NVDA":  SeedSpec(120.0, "tech",   sigma=0.50),
    "META":  SeedSpec(500.0, "tech"),
    "JPM":   SeedSpec(210.0, "finance", sigma=0.25),
    "V":     SeedSpec(275.0, "finance", sigma=0.20),
    "NFLX":  SeedSpec(650.0, "media",  sigma=0.40),
}

SECTORS = ("tech", "finance", "auto", "media", "other")
```

Six of the ten default tickers share `"tech"`, which is deliberate — it's what makes the sector
correlation (§2.3) visible immediately on a fresh install without the user adding anything.

### 3.2 Unknown tickers — deterministic hash seeding

Any tracked ticker without a `KNOWN_TICKERS` entry — a user typing a fresh symbol into the trade
bar, or the LLM adding one to the watchlist — needs a seed price with two properties: it must
look plausible ($20–$400, per `PLAN.md` §6), and it must be **the same every time**, across
restarts, with no persisted state, so re-adding a ticker later (or a second student running the
same symbol) sees consistent behavior.

A cryptographic hash of the ticker string, reduced into range, gives exactly that:

```python
import hashlib

def seed_for_unknown_ticker(ticker: str) -> SeedSpec:
    digest = hashlib.sha256(ticker.encode("utf-8")).digest()
    n = int.from_bytes(digest[:4], "big")          # 0 .. 2**32-1, uniform
    price = 20.0 + (n % 38000) / 100.0              # 20.00 .. 399.99, cents-resolution
    sector = SECTORS[digest[4] % len(SECTORS)]      # a 5th independent byte picks the sector
    return SeedSpec(price=price, sector=sector)


def spec_for(ticker: str) -> SeedSpec:
    return KNOWN_TICKERS.get(ticker) or seed_for_unknown_ticker(ticker)
```

Using separate hash bytes for price (`digest[:4]`) and sector (`digest[4]`) means the two are
independent of each other — two unknown tickers that happen to land near the same price aren't
also forced into the same sector. `sha256` is used purely for its uniform, well-distributed
output over `ticker`'s bytes — there is no security property being relied on here, any decent
hash would do; `sha256` is simply already in the standard library and collision-free enough that
"two different tickers get the same seed" is not a practical concern at watchlist scale.

`spec_for` is a pure function — same input, same output, forever — which is what makes it
trivially unit-testable (§8) and is the literal mechanism behind "deterministic across restarts."

---

## 4. The `SimulatorEngine` Class

The engine's public surface is exactly the two calls `MARKET_INTERFACE.md` §4.1's
`SimulatorProvider` makes: `sync_tracked(tickers)` to reconcile internal state with the current
tracked set, and `step(tickers)` to advance one tick and return fresh prices.

```python
# app/market_data/simulator_engine.py
from __future__ import annotations

import random
import time
from typing import AbstractSet

from .simulator_seeds import SECTORS, spec_for, SeedSpec

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
        """Advance one tick for exactly the given tickers and return their new prices.
        Assumes `sync_tracked(tickers)` has already been called this cycle (the
        `SimulatorProvider` in MARKET_INTERFACE.md §4.1 always does both together)."""
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
        """One shared roll per sector, per tick — every tracked ticker in that
        sector gets the same jump (or none) this tick (§2.4)."""
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

        drift = (spec.mu - 0.5 * spec.sigma ** 2) * dt
        diffusion = spec.sigma * (dt ** 0.5) * z
        new_price = price * pow(2.718281828459045, drift + diffusion)

        event_pct = sector_events[spec.sector]
        if event_pct is not None:
            new_price *= 1 + event_pct

        new_price = max(round(new_price, 2), MIN_PRICE)
        self._prices[ticker] = new_price
        return new_price
```

Design notes:

- **State lives in the engine, keyed by ticker, forever (until `sync_tracked` drops it).** This
  is what makes the walk a walk — each `step()` continues from the previous tick's price rather
  than re-rolling from the seed. Losing this state (e.g. constructing a new `SimulatorEngine`
  every cycle) would make every tick jump back to the seed price, destroying the whole effect.
- **`sector_factors` is drawn once per `step()` call, shared across every ticker passed in that
  call** — this is the entire correlation mechanism (§2.3) in three lines. No pairwise
  correlation matrix, no per-ticker bookkeeping beyond "which sector am I in."
- **`sector_events` is rolled the same way, once per `step()` call, shared across every ticker in
  that sector** (§2.4) — the same "one shared draw per sector" pattern as `sector_factors`, applied
  to jumps instead of diffusion. This is required, not stylistic symmetry: at this `dt`, a single
  event jump carries far more variance than a tick of diffusion, so an *independent* per-ticker
  roll would make uncorrelated jump noise dominate tick-to-tick returns and mask the §2.3
  correlation entirely — see the empirical test in §7.
- **A newly tracked ticker is seeded the moment it's first passed to `sync_tracked`,** and its
  very first `step()` call afterward already produces a real GBM-evolved price (not the bare seed
  price) — this satisfies "starts ticking on its next cycle" from `PLAN.md` §6 exactly, since the
  driver loop (`MARKET_INTERFACE.md` §5) calls `sync_tracked` then `fetch` (which calls `step`)
  every cycle, back to back.
- **Losing a ticker's `SimulatorEngine` state (removed and later re-tracked) restarts it from its
  deterministic seed price**, not from wherever it left off. This is intentional, not an
  oversight: `PLAN.md` gives no requirement to persist simulated price history across a ticker's
  absence from tracking, and doing so would need real persistence (contradicting "in-process
  background task — no external dependencies," §6). A ticker that leaves and rejoins the tracked
  set is, for simulation purposes, being seen "fresh."
- **`random.Random(seed)` accepts an optional seed** purely so tests can construct a
  `SimulatorEngine` with reproducible randomness (`SimulatorEngine(seed=42)`) — production code
  (`SimulatorProvider.__init__`, `MARKET_INTERFACE.md` §4.1) constructs it with no seed, i.e. true
  process-local randomness, since a demo that always ticks identically on every run would be a
  worse demo, not a better one. This is the only place "deterministic" is intentionally *not*
  the goal — contrast with `spec_for` (§3.2), which must be deterministic.

---

## 5. Numerical Safeguards

- **Price floor** (`MIN_PRICE = 0.01`): `max(round(new_price, 2), MIN_PRICE)` guarantees a price
  can never reach exactly zero or go negative, which would otherwise be possible (if unlikely)
  after a long enough sequence of down-ticks compounding multiplicatively toward zero, or a
  large negative event jump on an already-cheap ticker. A trade against a $0.00 price is exactly
  the "free shares" bug `PLAN.md` §8 warns about for the *Massive* unavailable case — the
  simulator must not be able to manufacture the same failure mode from its own math.
- **Rounding to cents** (`round(new_price, 2)`) before storing: real equity prices don't carry
  more than 2 decimal digits of precision, and rounding *before* the next tick's compounding
  (rather than only when displaying) keeps the internal state and the displayed price identical
  — there's no separate "raw" vs. "displayed" price to keep in sync.
- **`dt` is tiny** (`≈8.48e-8`), so `diffusion`'s magnitude per tick is small relative to `sigma`
  — this is what keeps individual ticks looking like normal market noise rather than the
  multi-percent swings that would result from treating `dt` as, say, "one full trading day."
  Event jumps (§2.4) are the intentional exception, layered on top precisely because pure GBM at
  this `dt` would otherwise never produce the "sudden 2–5% move" drama `PLAN.md` asks for.

---

## 6. Tuning Knobs

Every constant that shapes the simulator's feel is a module-level constant in
`simulator_engine.py`/`simulator_seeds.py`, not buried in the math — this table is the complete
list of what a future tuning pass would touch, and why:

| Constant | Default | Effect of increasing it |
|---|---|---|
| `DEFAULT_MU` | `0.05` | Stronger long-run upward bias across all tickers without a per-ticker override |
| `DEFAULT_SIGMA` | `0.35` | Noisier/choppier ticks for tickers without a per-ticker override |
| `IDIOSYNCRATIC_WEIGHT` | `0.6` | More per-ticker independence; sector-mates diverge more |
| `SECTOR_WEIGHT` | `0.8` | Tighter co-movement within a sector; less independent-looking noise |
| `EVENT_PROBABILITY` | `0.002` | More frequent 2–5% jump events (currently ~1 per ticker per 4 min) |
| `EVENT_MIN_PCT` / `EVENT_MAX_PCT` | `0.02` / `0.05` | Bigger/smaller jump size when an event fires |
| `TICK_INTERVAL_SECONDS` | `0.5` | Owned by `MARKET_INTERFACE.md`'s `poll_interval_seconds`, not the engine itself — changing this also changes `dt`, so `sigma`'s real-world meaning is preserved automatically |

---

## 7. Testing Strategy

Ties directly to the simulator-specific items in `PLAN.md` §12:

- **Determinism of unknown-ticker seeding**: `spec_for("ZZZZ")` called twice returns identical
  `SeedSpec`s (same object fields, not the same object); assert `price` is in `[20.0, 400.0)` by
  construction, for a batch of made-up symbols, not just one.
- **GBM math sanity**: seed a `SimulatorEngine(seed=42)` with one ticker, run `step()` many
  times (e.g. 10,000), and assert:
  - No price ever goes below `MIN_PRICE`.
  - The empirical mean of `log(price[t+1] / price[t])` over many steps is close to
    `(mu - sigma**2/2) * dt` within a wide statistical tolerance (this is a distributional check,
    not an exact-value assertion — use a generous tolerance band, since it's inherently a random
    process).
- **Correlation is present**: track two same-sector tickers and one different-sector ticker
  through the same `SimulatorEngine(seed=...)` for many steps; assert the same-sector pair's
  tick-to-tick returns have materially higher sample correlation than either has with the
  different-sector ticker. A fixed seed makes this a reproducible, non-flaky assertion.
  Known example: `SeedSpec` sectors already put AAPL/GOOGL/MSFT/AMZN/NVDA/META all in `"tech"` —
  a real test can use exactly this pair without inventing new fixtures. This assertion only holds
  because event jumps are rolled per-sector (§2.4), not per-ticker: with a per-ticker roll, the
  independent jumps dominate tick-level variance and the measured correlation collapses to
  ~0 regardless of how strongly `sector_factors` correlates the diffusion term — this is the
  entire reason `sector_events` exists.
- **Event jumps fire at roughly the configured rate**: over a large number of steps for one
  ticker with a fixed seed, count ticks where `abs(step_return) > EVENT_MIN_PCT` (a proxy for "an
  event fired that tick," since a pure-diffusion tick at this `dt` essentially never produces a
  2%+ move on its own) and assert the observed rate is in the right order of magnitude for
  `EVENT_PROBABILITY` — again a statistical, tolerance-banded assertion.
- **Newly tracked ticker prices immediately**: construct an engine, call
  `sync_tracked({"AAPL"})` then `step({"AAPL"})` — assert a valid float price comes back on the
  very first call, with no separate "warm-up" tick required.
- **Untracking and retracking resets to the seed**: `sync_tracked({"AAPL"})`, `step()` a few
  times so the price drifts from the seed, then `sync_tracked(set())` (drop it), then
  `sync_tracked({"AAPL"})` again — assert the internal price is back to exactly `spec_for("AAPL").price`,
  confirming the "fresh restart on re-track" behavior documented in §4 is real, not accidental.

---

## 8. Summary Checklist Against `PLAN.md` §6

- [x] GBM with configurable per-ticker drift/volatility (§2.2, §3.1).
- [x] ~500ms cadence — owned by the driver (`MARKET_INTERFACE.md` §5) via
      `SimulatorProvider.poll_interval_seconds`, honored by the engine through its `dt`
      calibration (§2.2).
- [x] Correlated moves across related tickers via a shared per-sector factor (§2.3).
- [x] Occasional 2–5% event jumps (§2.4).
- [x] Known tickers seeded at realistic prices (§3.1).
- [x] Unknown tickers deterministically seeded in $20–$400 from a hash of the symbol, stable
      across restarts (§3.2).
- [x] A newly tracked ticker is seeded and priced on its very next cycle, no warm-up delay (§4).
- [x] In-process only — no network, no DB, no external dependency (§4, entire engine).

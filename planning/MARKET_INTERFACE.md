# Unified Market Data Interface — Detailed Design

## Relationship to the other planning documents

- **`MASSIVE_API.md`** — what Massive's wire API actually looks like (endpoints, auth, JSON
  shapes, rate limits), independent of this project.
- **`MARKET_SIMULATOR.md`** — how the built-in simulator generates prices (GBM, correlation,
  seeding), independent of this project's plumbing.
- **This document (`MARKET_INTERFACE.md`)** — the Python contract that lets the rest of the
  backend (SSE stream, trade executor, portfolio valuation) ask for prices **without knowing or
  caring** whether Massive or the simulator is answering.
- **`market-data-design.md`** — the fuller subsystem design (shared cache, tracked-set
  computation, SSE diffing, REST endpoints, task supervision). It's still authoritative for all
  of that. This document **refines one part of it**: `market-data-design.md` §6 gave `Simulator­Source`
  and `MassiveSource` each their own `run()` loop, and the two loops duplicated the same
  tracked-set-refresh-then-sleep shape. This document extracts that loop into one shared driver
  (§5 below) and narrows each source down to a single pure method — `fetch()`. Everything else in
  `market-data-design.md` (the cache, the tracked set, the SSE route, the supervisor) is unchanged
  and this document builds directly on it.

If you're implementing this project, treat `MARKET_INTERFACE.md` as the current word on the
provider abstraction and `market-data-design.md` §6 as background/rationale rather than the
literal class shapes to type in.

---

## 1. The Problem This Interface Solves

`PLAN.md` §5/§6 states the rule plainly: **if `MASSIVE_API_KEY` is set, use Massive; otherwise use
the simulator** — and nothing downstream should need to know which one is active. Concretely,
that means the SSE stream, the trade executor, and the portfolio valuator must all be able to
say "give me current prices for these tickers" through one call shape, regardless of source.

The two sources are naturally very different in cost and cadence:

| | Simulator | Massive |
|---|---|---|
| Cost per "tick" | Free, in-process math | A real HTTP call, rate-limited (`MASSIVE_API.md` §8) |
| Natural cadence | ~500ms | 15s (free tier) up to 2s (paid) |
| Always has an answer? | Yes — every tracked ticker gets a price every cycle | No — a ticker can be absent from a response (`MASSIVE_API.md` §7) |

The interface below is designed so those differences live entirely inside each source's `fetch()`
implementation, and nowhere else.

---

## 2. The `Quote` Data Model

A `fetch()` call returns, for each ticker it has data for, a `Quote`. Tickers it has **no** data
for this cycle are simply absent from the returned mapping — there is no `Quote(price=None)`
sentinel, because "absent" already means exactly one thing (§7 below) and a nullable-price object
would let a bug construct a nonsensical `Quote(price=None, reference_price=180.0)`.

```python
# app/market_data/provider.py
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ReferenceKind(str, Enum):
    PREV_CLOSE = "prev_close"
    SESSION_OPEN = "session_open"


@dataclass(frozen=True, slots=True)
class Quote:
    """One source's answer for one ticker, for one fetch() call."""
    ticker: str
    price: float
    reference_price: Optional[float] = None
    reference_kind: Optional[ReferenceKind] = None
```

- `price` is always a real, finite, positive float when a `Quote` exists — a source must never
  construct a `Quote` for a ticker it isn't confident about (§8 has the exact rules for both
  implementations).
- `reference_price`/`reference_kind` are optional per-call. When a source doesn't know a
  reference for this ticker yet (the simulator's very first tick for it; Massive when `prevDay`
  is briefly absent), it omits them and the cache's existing session-open-seeding behavior
  (`market-data-design.md` §4, `_seed_reference`) takes over unchanged. A source never has to
  compute "is this the first time I've seen this ticker" itself — the cache already owns that.

---

## 3. The `MarketDataProvider` Interface

```python
# app/market_data/provider.py (continued)
from abc import ABC, abstractmethod
from typing import AbstractSet, Mapping


class MarketDataProvider(ABC):
    """
    The one interface every price source implements. `fetch()` is a single
    request/response round — it does not loop, sleep, or own a schedule.
    Scheduling is the driver's job (§5), not the provider's, which is what
    makes both implementations trivially unit-testable: call `fetch()` once
    with a known ticker set and assert on the dict you get back.
    """

    #: How often the driver should call `fetch()` for this provider, in
    #: seconds. A class attribute (constant) for the simulator; an
    #: instance attribute for Massive, since it's derived from
    #: MASSIVE_POLL_INTERVAL_SECONDS at construction time.
    poll_interval_seconds: float

    @abstractmethod
    async def fetch(self, tickers: AbstractSet[str]) -> Mapping[str, Quote]:
        """
        Fetch current data for exactly the given tickers, once.

        Returns a dict keyed by ticker. A ticker in `tickers` that is absent
        from the returned dict means the source has no data for it *right
        now* — see §7 for exactly what that means for each implementation.
        Never raises for an individual bad/unknown ticker; only raises for a
        whole-request failure (network error, auth failure, malformed
        response) that the caller should treat as "this cycle produced
        nothing usable, try again next cycle" (§6).
        """
        raise NotImplementedError
```

This is intentionally a **pull, one-shot** contract — `fetch(tickers) -> {ticker: Quote}` — rather
than a `run()` method that owns a loop and writes into the cache itself. That's the concrete
refinement over `market-data-design.md` §6 flagged above: putting the loop in one shared driver
(§5) instead of in each implementation means adding a third source later (say, a different
paid data vendor) only requires implementing `fetch()`; the polling, tracked-set refresh, cache
writes, and unavailable-marking are already correct by construction.

---

## 4. The Two Implementations

### 4.1 `SimulatorProvider`

Thin adapter over the GBM engine detailed in `MARKET_SIMULATOR.md` — this class owns none of the
math, only the `MarketDataProvider` contract:

```python
# app/market_data/simulator_provider.py
from __future__ import annotations

import time
from typing import AbstractSet, Mapping

from .provider import MarketDataProvider, Quote, ReferenceKind
from .simulator_engine import SimulatorEngine  # see MARKET_SIMULATOR.md


class SimulatorProvider(MarketDataProvider):
    poll_interval_seconds: float = 0.5

    def __init__(self) -> None:
        self._engine = SimulatorEngine()

    async def fetch(self, tickers: AbstractSet[str]) -> Mapping[str, Quote]:
        # Pure CPU-bound math, no I/O — safe to call directly from the
        # driver's async loop without a thread hop.
        self._engine.sync_tracked(tickers)
        prices = self._engine.step(tickers)  # {ticker: float}, one entry per input ticker

        # No reference_price/reference_kind here on purpose: the simulator
        # never has a real previous close, so it always wants the cache's
        # own "first price observed becomes the session-open reference"
        # behavior (market-data-design.md §4, `_seed_reference`) rather than
        # duplicating that bookkeeping inside the engine. This also means a
        # ticker that drops out of the tracked set and later rejoins gets a
        # fresh session-open reference at whatever price it restarts from —
        # correct, since its old reference no longer describes anything.
        return {
            ticker: Quote(ticker=ticker, price=price)
            for ticker, price in prices.items()
        }
```

The simulator **always** returns every requested ticker — GBM never "has no data." `MARKET_SIMULATOR.md`
covers `SimulatorEngine` (seeding, correlation, event jumps) in full; nothing about that math
belongs in this document.

### 4.2 `MassiveProvider`

Thin adapter over the REST call documented in `MASSIVE_API.md` §5.1/§9:

```python
# app/market_data/massive_provider.py
from __future__ import annotations

import logging
from typing import AbstractSet, Mapping

import httpx

from .provider import MarketDataProvider, Quote, ReferenceKind

logger = logging.getLogger("market_data.massive")

MASSIVE_BASE_URL = "https://api.massive.com"
SNAPSHOT_PATH = "/v2/snapshot/locale/us/markets/stocks/tickers"


class MassiveProvider(MarketDataProvider):
    def __init__(self, api_key: str, poll_interval_seconds: float = 15.0) -> None:
        self.poll_interval_seconds = poll_interval_seconds
        self._client = httpx.AsyncClient(
            base_url=MASSIVE_BASE_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=8.0,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch(self, tickers: AbstractSet[str]) -> Mapping[str, Quote]:
        if not tickers:
            return {}

        response = await self._client.get(
            SNAPSHOT_PATH, params={"tickers": ",".join(sorted(tickers))}
        )
        response.raise_for_status()  # network/auth/rate-limit failures propagate to the driver (§6)
        payload = response.json()

        quotes: dict[str, Quote] = {}
        for row in payload.get("tickers", []):
            quote = _quote_from_row(row)
            if quote is not None:
                quotes[quote.ticker] = quote
        return quotes
        # Tickers requested but absent from payload["tickers"] are simply
        # not in the returned dict — that IS the "unavailable" signal (§7).
        # This function does not need to know the full `tickers` set to
        # produce a correct result; the driver (§5) computes the difference.


def _quote_from_row(row: dict) -> "Quote | None":
    ticker = row.get("ticker")
    last_trade = row.get("lastTrade") or {}
    price = last_trade.get("p")
    if not ticker or price is None:
        return None  # malformed row for this ticker; treat like "absent"

    prev_close = (row.get("prevDay") or {}).get("c")
    return Quote(
        ticker=ticker,
        price=float(price),
        reference_price=float(prev_close) if prev_close is not None else None,
        reference_kind=ReferenceKind.PREV_CLOSE if prev_close is not None else None,
    )
```

Every field access here (`lastTrade.p`, `prevDay.c`, the `tickers` wrapper key) is exactly what
`MASSIVE_API.md` §5.1 documents from the real recorded response — this function is a direct,
minimal transcription of that shape into a `Quote`.

---

## 5. The Shared Driver Loop

One function drives *either* provider — this is what replaces the duplicated `run()` bodies from
`market-data-design.md` §7/§8:

```python
# app/market_data/driver.py
from __future__ import annotations

import asyncio
import time

from .cache import PriceCache
from .provider import MarketDataProvider
from .tracked_set import TrackedSetProvider


async def run_market_data_loop(
    provider: MarketDataProvider,
    cache: PriceCache,
    tracked_set: TrackedSetProvider,
) -> None:
    """
    Runs forever: recompute the tracked set, ask the provider for a fresh
    fetch() of exactly that set, and reconcile the result into the shared
    cache. Wrapped by run_supervised() (market-data-design.md §9) so a
    transient exception here (e.g. a Massive network blip) restarts this
    loop with backoff instead of freezing the cache forever.
    """
    while True:
        tracked = await tracked_set.get()
        cache.drop_untracked(tracked)
        for ticker in tracked:
            cache.ensure_tracked(ticker)  # visible as "pending" immediately

        try:
            quotes = await provider.fetch(frozenset(tracked))
        except Exception:
            # Whole-request failure: leave the cache exactly as it was and
            # retry next cycle. This mirrors MASSIVE_API.md §7's rule that a
            # transport failure must never be conflated with a per-ticker
            # "no data" result, which IS handled below via `quotes.get(...)`.
            logging.getLogger("market_data.driver").warning(
                "%s.fetch() failed; cache left unchanged this cycle",
                type(provider).__name__, exc_info=True,
            )
            await asyncio.sleep(provider.poll_interval_seconds)
            continue

        now = time.time()
        for ticker in tracked:
            quote = quotes.get(ticker)
            if quote is None:
                cache.mark_unavailable(ticker)
            else:
                cache.update(
                    ticker,
                    quote.price,
                    reference_price=quote.reference_price,
                    reference_kind=quote.reference_kind,
                    now=now,
                )

        await asyncio.sleep(provider.poll_interval_seconds)
```

Notes:

- This is the **only** place that calls `provider.fetch()`, the **only** place that owns
  `asyncio.sleep(provider.poll_interval_seconds)`, and the **only** place that talks to
  `TrackedSetProvider` and `PriceCache` directly. Both `SimulatorProvider` and `MassiveProvider`
  are fully decoupled from all three.
- The `quotes.get(ticker)` lookup is precisely how "absent from the fetch result" becomes
  `cache.mark_unavailable(ticker)` — one line, shared by both sources, instead of each source
  needing its own unavailable-marking logic (which `market-data-design.md`'s per-source `run()`
  duplicated).
- A whole-`fetch()` failure (network error, `raise_for_status()`, JSON decode error) is caught
  once, here, and treated as "no-op this cycle" — not as "every tracked ticker is now
  unavailable." That distinction is the same one `MASSIVE_API.md` §7 makes at the wire level;
  this is where it's enforced in code.

---

## 6. Selecting a Provider: the Factory

Exactly one switch, checked once at startup — matching `PLAN.md` §5 verbatim:

```python
# app/market_data/factory.py
from __future__ import annotations

from ..config import Settings
from .massive_provider import MassiveProvider
from .provider import MarketDataProvider
from .simulator_provider import SimulatorProvider


def build_market_data_provider(settings: Settings) -> MarketDataProvider:
    if settings.massive_api_key:
        return MassiveProvider(
            api_key=settings.massive_api_key,
            poll_interval_seconds=settings.massive_poll_interval_seconds,
        )
    return SimulatorProvider()
```

`Settings.massive_api_key` is `""` when the environment variable is unset or blank (see
`market-data-design.md` §10 for the `Settings.from_env()` implementation) — an empty string is
falsy, so `if settings.massive_api_key:` is the entire selection rule, with no separate
"is this configured" flag to keep in sync.

### Wiring at startup

```python
# app/main.py (excerpt — full lifespan shown in market-data-design.md §11)
provider = build_market_data_provider(settings)
tasks.append(
    asyncio.create_task(
        run_supervised(
            "market_data",
            lambda: run_market_data_loop(provider, cache, tracked_set),
        )
    )
)
```

`run_supervised` (defined in `market-data-design.md` §9) is provider-agnostic too — it just
restarts whatever coroutine factory it's given, so it needs no changes to work with either
provider through the same `run_market_data_loop` driver.

---

## 7. What "Unavailable" Means, Concretely, Per Source

This is the crux of the interface contract, so it's worth stating once, precisely, per source:

| Source | A ticker is "unavailable" when... | Enforced by |
|---|---|---|
| Simulator | Never — every tracked ticker gets a deterministic-or-GBM price every `fetch()` call, per `MARKET_SIMULATOR.md`. | `SimulatorProvider.fetch()` always returns all requested tickers. |
| Massive | The ticker is absent from `payload["tickers"]` in an otherwise-`200 OK` response — a real, confirmed "no data for this symbol right now," per `MASSIVE_API.md` §5.1/§7. | `MassiveProvider.fetch()` only includes tickers actually present in the response; `run_market_data_loop`'s `quotes.get(ticker) is None` check does the rest. |
| Either | The whole `fetch()` call raised (network error, timeout, bad auth, malformed JSON). | **This is explicitly NOT "unavailable."** `run_market_data_loop` catches this separately and leaves cache state untouched for a retry next cycle, rather than marking every tracked ticker unavailable off one transient failure. |

This table is the answer to the trade-execution question in `PLAN.md` §8: `cache.get(ticker).status == "unavailable"`
can only mean "the active source affirmatively told us it has nothing for this ticker," never
"we couldn't reach the source this cycle." A `409` on a trade should therefore be exactly as
common as the tracked ticker genuinely lacking data — not inflated by network hiccups.

---

## 8. Testing the Interface

Because `fetch()` is pure request/response with no scheduling or cache side effects, both
providers and the driver are testable independently and cheaply:

```python
# test_market_data/test_provider_contract.py
import pytest
from app.market_data.provider import MarketDataProvider, Quote, ReferenceKind


class FakeProvider(MarketDataProvider):
    """A hand-rolled provider for testing run_market_data_loop() without
    real GBM math or a real HTTP call."""
    poll_interval_seconds = 0.0

    def __init__(self, answers: dict[str, Quote]) -> None:
        self._answers = answers

    async def fetch(self, tickers):
        return {t: q for t, q in self._answers.items() if t in tickers}


@pytest.mark.asyncio
async def test_missing_ticker_marks_unavailable(price_cache, tracked_set_stub):
    # AAPL has a quote queued up; MSFT does not — simulates Massive omitting
    # a symbol from its response.
    provider = FakeProvider({"AAPL": Quote(ticker="AAPL", price=190.0)})
    tracked_set_stub.set({"AAPL", "MSFT"})

    await _run_one_cycle(provider, price_cache, tracked_set_stub)  # test helper: one loop body, no sleep

    assert price_cache.get("AAPL").status == "ok"
    assert price_cache.get("MSFT").status == "unavailable"
```

```python
# test_market_data/test_massive_provider.py
import respx
import httpx
import pytest
from app.market_data.massive_provider import MassiveProvider

@pytest.mark.asyncio
@respx.mock
async def test_fetch_parses_snapshot_response():
    respx.get("https://api.massive.com/v2/snapshot/locale/us/markets/stocks/tickers").mock(
        return_value=httpx.Response(200, json={
            "status": "OK",
            "tickers": [
                {"ticker": "AAPL", "lastTrade": {"p": 190.12}, "prevDay": {"c": 188.5}},
            ],
        })
    )
    provider = MassiveProvider(api_key="test-key")
    quotes = await provider.fetch({"AAPL", "TSLA"})  # TSLA absent from the mocked response

    assert quotes["AAPL"].price == 190.12
    assert quotes["AAPL"].reference_price == 188.5
    assert "TSLA" not in quotes  # <- the unavailable case, asserted at the provider boundary
```

This is a direct instance of the `MASSIVE_API.md` §5.1 fixture JSON (§5.1's `index.json`
recorded response) shrunk to the two fields this project actually reads — using the same
shape means a schema change on Massive's side would be caught by a test built against a real
recorded payload, not an invented one.

Neither test needs a live API key, a running simulator, or the FastAPI app — this is the payoff
of keeping `fetch()` a pure function of `(tickers) -> {ticker: Quote}`.

---

## 9. Summary Checklist

- [x] One interface (`MarketDataProvider.fetch()`), two implementations, zero duplicated
      scheduling code (§3–§5).
- [x] Source selection is `if settings.massive_api_key: ... else: ...`, checked once at startup,
      exactly matching `PLAN.md` §5 (§6).
- [x] "Unavailable" is defined precisely per source and kept separate from "the request failed"
      (§7), closing the gap `MASSIVE_API.md` §7 flags between a per-ticker `NOT_FOUND`-shaped
      absence and a transport-level error.
- [x] Both providers are unit-testable with no network and no real GBM engine, using fakes that
      implement the same three-line contract (§8).
- [x] `SimulatorProvider` and `MassiveProvider` each own only translation into `Quote` — the
      actual GBM math lives in `MARKET_SIMULATOR.md`, the actual wire format lives in
      `MASSIVE_API.md`, and this document owns neither.

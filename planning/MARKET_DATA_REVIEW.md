# Market Data Backend — Code Review

Reviewer: Claude (automated code review), 2026-08-08
Scope: `backend/app/market_data/` + `backend/app/config.py`, against `PLAN.md` §5, §6, §8, §12,
`market-data-design.md`, `MARKET_INTERFACE.md`, `MARKET_SIMULATOR.md`, `MASSIVE_API.md`, and the
test suite in `backend/tests/market_data/`.

## TL;DR

The implementation is a faithful, well-structured realization of `MARKET_INTERFACE.md` (the
current-word design doc) — models, cache, tracked set, provider interface, both providers, the
shared driver, the supervisor, and the factory are all present, cleanly separated, and covered by
69 passing tests. One real correctness bug was found in `PriceCache.update()` (detailed below):
it does not check whether the incoming price actually differs from the cached price before
advancing `updated_at` and appending to `history`, which contradicts an invariant stated in the
code's own comments and in `market-data-design.md`, and is empirically **not** the rare edge case
the design doc assumes — it happens on 17–84% of simulator ticks depending on the ticker's price
level. Everything else is minor or forward-looking (the SSE/history HTTP endpoints and FastAPI
lifespan wiring described in `market-data-design.md` §11–§13 don't exist yet — expected, since
that's explicitly a different module's concern per §1, but worth flagging so it isn't assumed done).

**Status: ALL FINDINGS RESOLVED (2026-08-08).** Finding #1 was fixed first; Findings #2, #3, and
the actionable part of #4 were closed in a follow-up pass; a third pass closed the in-scope items
from that pass's own "remaining limitations" list. See the Resolution sections and the
**Final Status Table** below.

## Test Results (original review)

```
cd backend && uv sync && uv run pytest tests/market_data -v
...
69 passed in 1.02s
```

All 69 tests pass, with no warnings besides an unrelated `pyproject.toml` deprecation notice
(`tool.uv.dev-dependencies` → `dependency-groups.dev`, a `uv` packaging convention change,
unrelated to this module's code). Coverage maps cleanly onto the module-specific bullets in
`PLAN.md` §12: cache reference/status semantics, tracked-set union/epsilon behavior, both
providers' interface conformance, Massive response parsing (including malformed/non-finite/
non-positive price rejection), simulator GBM sanity (drift, event-jump rate, sector correlation),
unknown-ticker determinism, and supervisor backoff/restart behavior.

## Findings

### 1. `PriceCache.update()` doesn't check whether the price actually changed — confirmed, real impact

**Status: RESOLVED.**

**File:** `backend/app/market_data/cache.py`, lines 45–69
**Also see:** `backend/app/market_data/models.py` line 51; `planning/market-data-design.md` §4 and §7.3

`CacheEntry.updated_at` is documented — in the model's own field comment and in the design doc —
as advancing only on a real price change:

```python
# models.py:51
updated_at: float = 0.0   # epoch seconds of the last *change* to `price`
```

But `PriceCache.update()` didn't compare the incoming `price` to `entry.price` before mutating:

```python
# cache.py:62-69 (before fix)
entry.prev_price = entry.price if entry.price is not None else price
entry.price = price
entry.status = TickerStatus.OK
entry.updated_at = now                                  # <-- advances unconditionally
entry.history.append(PricePoint(t=now, price=price))    # <-- appended unconditionally
```

It is called once per tracked ticker per driver cycle (`driver.py`'s `run_market_data_cycle`)
regardless of whether the new `Quote.price` differs from what's already cached.
`market-data-design.md` §7.3 explicitly anticipates this exact question and waves it off:

> "cache.update() is called every cycle for every tracked ticker (not only when the price
> "meaningfully" changes) — GBM almost never produces an exact repeat, so this is fine... If a
> future tuning pass makes ticks that round to the same 2-decimal price common, skip the
> update() call in that case..."

That assumption — "GBM almost never produces an exact repeat" — is false at the module's own
default tuning constants. `TICK_INTERVAL_SECONDS=0.5` gives `dt ≈ 8.48e-8`, so a tick's diffusive
price move has standard deviation `price × sigma × sqrt(dt)`, which is well under one cent for
lower-priced tickers. Measured directly against `SimulatorEngine` with the shipped defaults
(seeded RNG, 20,000 ticks each):

| Ticker | Seed price | sigma | Fraction of ticks with **identical** rounded price to the previous tick |
|---|---|---|---|
| unknown ~$30 symbol | 30.28 | 0.35 | **84.4%** |
| unknown ~$121 symbol | 121.07 | 0.35 | 30.0% |
| JPM | ~$200 | 0.25 | 26.2% |
| V | ~$268 | 0.20 | 24.9% |
| GOOGL | ~$221 | 0.35 | 20.7% |
| AAPL | ~$240 | 0.35 | 19.2% |

(Reproduction: construct `SimulatorEngine(seed=N)`, call `sync_tracked`/`step` in a loop, compare
consecutive prices.) For a $20–$40 ticker — squarely inside the documented unknown-ticker seed
range of §7.2 — over four out of every five ticks produce the exact same price as the tick before.

Concrete consequences, both live at the time of the review:

- **`history` ring buffer pollution.** `HISTORY_MAXLEN=2000` is sized as "~an hour of simulator
  ticks" on the assumption that each entry is a distinct price observation. For a low-priced
  ticker, a large fraction of that buffer is consumed by consecutive duplicate points, so the
  detail-chart backfill (`GET /api/prices/{ticker}/history`, not yet implemented but consuming
  this buffer once it is) covers meaningfully less wall-clock time than intended — worst case
  ~6x less for the 84%-duplicate example above.
- **Would silently defeat the SSE dedup contract once §12 is implemented.** `PLAN.md` §6 and
  `market-data-design.md` §12 both specify the stream emits a ticker's event "only when that
  ticker's `updated_at` has advanced" specifically so unchanged prices produce no traffic (called
  out for the Massive 15s-poll case, but the invariant is stated generally). Since `updated_at`
  advanced on every driver cycle regardless of an actual price change, once the SSE route is
  wired up it would have re-emitted a "new" event for a plainly unchanged price on a large
  fraction of ticks — most visibly on cheaper tickers, which is also where the demo's "prices
  flash green/red" effect (`PLAN.md` §2) would look the most broken, since a real flash requires
  `direction != "flat"`, and these repeat-ticks never produce one.

**Fix applied:** in `PriceCache.update()`, compare `price` against `entry.price` first. If equal
and `entry.status` is already `OK`, `updated_at` and `history` are left untouched (a later poll
can still supply a previously-missing `reference_price`/`reference_kind`, so that logic still
runs unconditionally). `prev_price` is still assigned on every call — including no-op repeats —
so `direction` correctly reports `"flat"` for a repeated tick rather than continuing to compare
against a stale prior price. If the ticker is transitioning into `OK` from `pending` or
`unavailable` (including on an identical price to the last known one), `updated_at` and `history`
still advance, since that transition is itself a real observation the SSE layer and the detail
chart need to see. The fix is confined to `cache.py`, matching the existing invariant claims in
`models.py`/`market-data-design.md`.

### 2. SSE stream, history endpoint, and FastAPI lifespan wiring are not implemented yet

**Status: RESOLVED** — implemented in the follow-up pass; see below.

**Files:** none present — no `backend/app/api/`, no `backend/app/main.py`, no `backend/app/db/`

`market-data-design.md` §1 lists "the SSE stream... and the history endpoint" as owned by this
subsystem, and §11–§13 give full implementations. None of that exists in the current tree —
`build_market_data_provider`, `run_market_data_loop`, and `run_supervised` are ready to be wired
into a lifespan handler, but nothing calls them yet, and there's no HTTP surface at all (no FastAPI
app, no DB init). This is presumably intentional — the primitives this doc describes (cache,
tracked set, providers, driver, supervisor) are exactly what a subsequent PR wiring up the API
layer would need — but it means none of the `PLAN.md` §12 SSE/history-endpoint test bullets ("SSE:
unchanged tickers emit no events," "new connection receives a full snapshot," "History endpoint:
`since`/`limit` bounds hold") can exist yet, and Finding #1 above should be fixed *before* that
endpoint is built, since the endpoint's correctness depends on `updated_at` behaving as documented.

Not a defect in what exists — flagged so the "Market Data Backend has been implemented" framing
doesn't get read as "the SSE/history endpoints are implemented," which they aren't.

### 3. Minor: no `aclose()` path for `MassiveProvider` once lifespan wiring exists

**Status: RESOLVED** — `aclose()` is now part of the base interface; see below.

**File:** `backend/app/market_data/massive_provider.py`

`MassiveProvider` opens an `httpx.AsyncClient` in `__init__` and exposes `aclose()`, but
`MarketDataProvider` (the abstract base every consumer codes against) has no `aclose`/shutdown
hook, and nothing currently calls `MassiveProvider.aclose()` outside its own tests. Once the
lifespan handler in Finding #2 is written, it will need either an `isinstance(provider,
MassiveProvider)` special-case or a widened interface (e.g. an optional `async def aclose(self) ->
None: pass` on the base class) to avoid leaking the client's connection pool on shutdown. Low
severity — `SimulatorProvider` has no such resource, and this only matters once `MASSIVE_API_KEY`
is set in a real deployment — but worth deciding now rather than discovering it during the API-layer PR.

### 4. Minor observations (no action needed)

- `TrackedSetProvider.get()` opens and closes a fresh `aiosqlite` connection every call (every
  ~500ms for the simulator). This matches the design doc's explicit rationale (§5: "one source of
  truth, zero drift risk") and SQLite connection overhead is small at this scale, but it's worth
  knowing this is a deliberate simplicity-over-throughput tradeoff if profiling ever flags it.
  **Unchanged** — still a deliberate tradeoff, not touched by the follow-up pass.
- `massive_provider.py`'s `_as_finite_positive_float` guard (rejecting non-numeric, non-finite,
  zero, and negative prices from the wire) is exactly the defense `PLAN.md` §8 calls for against
  a fabricated-price trade fill, and it's well covered by `test_massive_provider.py` (malformed
  rows, `NaN`, zero/negative prices, non-numeric strings all tested independently). This is one of
  the stronger parts of the implementation.
- `SimulatorEngine`'s per-sector shared event roll (`_roll_sector_event`, one draw per sector per
  `step()`, not per ticker) correctly implements the subtle correlation-preserving fix documented
  in `MARKET_SIMULATOR.md` §2.4, and `test_same_sector_tickers_are_more_correlated_than_cross_sector`
  actually verifies the statistical claim rather than just checking the code path runs.
- No CI workflow currently runs `backend/tests` (the only workflows present are the Claude
  code-review/dispatch actions, not a test runner). Not this module's concern to fix, but worth
  noting since "tests exist and pass locally" and "tests run in CI" are different guarantees.
  **RESOLVED** — a `Backend Tests` workflow was added in the follow-up pass.

## What's Solid

- **Interface discipline.** `MarketDataProvider.fetch()` is a pure, one-shot `(tickers) -> {ticker:
  Quote}` call with no scheduling or cache access, exactly as `MARKET_INTERFACE.md` specifies. Both
  `SimulatorProvider` and `MassiveProvider` are consequently trivial to unit test in total isolation
  from the cache, the DB, and each other — which the test suite exploits well (`FakeProvider` in
  `test_driver.py`, `respx`-mocked HTTP in `test_massive_provider.py`).
- **The "unavailable" contract is precise and tested from both directions.** A whole-request
  failure (network error, non-2xx, malformed JSON) leaves cache state untouched and is never
  conflated with a per-ticker absence (`test_whole_fetch_failure_leaves_cache_unchanged`); a ticker
  genuinely missing from an otherwise-successful response is marked `unavailable` without touching
  its last-known price (`test_missing_ticker_marks_unavailable`,
  `test_mark_unavailable_preserves_last_known_price`). This is the exact distinction
  `MASSIVE_API.md` §7 and `PLAN.md` §8's "free shares" warning depend on, and it holds.
- **Deterministic unknown-ticker seeding is genuinely deterministic and tested as such** —
  `test_unknown_ticker_seeding_is_deterministic`, plus independent price/sector byte selection
  tested directly, not just asserted by inspection.
- **Supervisor backoff/restart is tested precisely**, including the exponential-with-cap sequence
  (`test_backoff_grows_and_is_capped` asserts the literal `[1.0, 2.0, 3.0]` sleep sequence) and that
  `CancelledError` is never swallowed — the one failure mode that would make shutdown hang.
- **Tracked-set epsilon handling matches `PLAN.md` §7 exactly**, including the specific residual-
  quantity case (`1e-13` position not tracked) that the plan calls out as the reason for the epsilon
  in the first place.

## Resolution (2026-08-08) — Finding #1

Finding #1 has been fixed in `backend/app/market_data/cache.py`. `PriceCache.update()` now
compares the incoming price against the currently cached price (and current `status`) before
mutating:

- If the price is unchanged **and** the entry is already `TickerStatus.OK`, the update is a
  no-op for `updated_at` and `history` — neither advances/appends. `reference_price`/
  `reference_kind` still update unconditionally, since a later poll can legitimately supply a
  previously-missing reference. `prev_price` is still reassigned every call, so `direction`
  correctly reports `"flat"` on a repeated tick instead of comparing against a stale value.
- If the price differs from what's cached, or the entry is transitioning into `OK` from
  `pending`/`unavailable` (even at an identical price to the last known one), `updated_at` and
  `history` advance as before — that transition is itself a real observation.

The public interface of `PriceCache.update()` is unchanged (same signature, same call sites in
`driver.py`); the fix is confined entirely to `cache.py`.

New tests were added to `backend/tests/market_data/test_cache.py`:

- `test_repeated_identical_price_does_not_advance_updated_at`
- `test_repeated_identical_price_does_not_append_duplicate_history`
- `test_repeated_identical_price_keeps_status_ok`
- `test_real_change_after_repeats_advances_updated_at_and_appends_history`
- `test_repeated_price_still_allows_reference_update`
- `test_recovering_from_unavailable_with_same_price_advances_updated_at`

No existing test was weakened or removed. The one existing test whose semantics interact directly
with this change, `test_direction_property` (two consecutive identical `update()` calls asserting
`direction == "flat"`), continues to pass unmodified because `prev_price` is still reassigned on a
no-op repeat.

**Note on the original fix's verification:** the agent that applied the Finding #1 fix could not
execute `uv`/`pytest` in its sandboxed environment and verified the change by manual trace only,
flagging that a follow-up run should confirm the suite. **That confirmation has now happened** —
the fix's 6 new tests and all 69 pre-existing tests pass (75 total), exactly as predicted. See the
final test results below.

## Resolution — follow-up pass (2026-08-08) — Findings #2, #3, #4

### Finding #3 — `aclose()` is now part of the provider interface

`MarketDataProvider` (`backend/app/market_data/provider.py`) gained a concrete, no-op
`async def aclose(self) -> None`. It is deliberately **not** abstract: `SimulatorProvider` owns no
releasable resource, and forcing every implementation to write an empty override buys nothing.
`MassiveProvider.aclose()` now documents itself as an override that closes the `httpx.AsyncClient`
connection pool.

The payoff is in the lifespan handler, which shuts down whatever provider is active with a plain
`await provider.aclose()` — no `isinstance(provider, MassiveProvider)` special case, and a third
source added later gets correct shutdown for free.

Covered by the new `backend/tests/market_data/test_provider_contract.py` (11 tests), which
parametrizes the shared contract across both shipped implementations: `aclose()` exists on the
base class, is not abstract, is idempotent, actually closes Massive's HTTP client, leaves the
simulator usable, and is inherited by a third-party subclass that never defines it.

### Finding #2 — SSE stream, history endpoint, and lifespan wiring implemented

**New files:**

| File | Contents |
|---|---|
| `backend/app/api/__init__.py`, `backend/app/api/routes/__init__.py` | Package markers; the dependency direction stays one-way (`api/` → `market_data/`, never the reverse, per `market-data-design.md` §2). |
| `backend/app/api/routes/market_data.py` | `GET /api/stream/prices` and `GET /api/prices/{ticker}/history`, per design §12/§13. |
| `backend/app/main.py` | `create_app()` + the lifespan handler, per design §11. |

**`GET /api/stream/prices`.** Implemented as specified: a full snapshot of every tracked ticker on
connect (so a fresh or reconnecting `EventSource` is populated without waiting for a tick), then a
ticker's event only when its `updated_at` has advanced since the last event sent *on that
connection*, checked at 500ms, with a `: ping` comment heartbeat every 15s and a
`request.is_disconnected()` exit.

One deliberate refinement over design §12.2: the generator body is a module-level
`price_event_stream(cache, is_disconnected, *, check_interval_seconds, heartbeat_interval_seconds)`
rather than a closure inside the route handler. Behavior is identical — `last_sent_at` is still
per-connection local state — but as a plain async generator over a `PriceCache` it can be driven
directly in tests, which is exactly what design §15 asks for ("it can be driven in a test by
calling `.__anext__()` manually rather than running the full `StreamingResponse`"). The route
handler is now a four-line wrapper that supplies the cache, the disconnect callback, and the SSE
headers.

**`GET /api/prices/{ticker}/history`.** Per design §13: `404` for an untracked ticker, otherwise
`{ticker, reference_price, reference_kind, points}` with points oldest-first from the in-memory
ring buffer. The ticker path parameter is normalized (`.strip().upper()`). An empty buffer on a
fresh container is a `200` with `points: []`, not an error. Note this endpoint takes no
`since`/`limit` parameters — those belong to `/api/portfolio/history` in `PLAN.md` §8, which is the
portfolio module's endpoint, not this one. The bound here is structural: the `deque(maxlen=2000)`.

**Lifespan wiring.** `create_app(settings=None)` builds the app (settings are injectable so tests
can point the tracked set at a temporary database); the lifespan constructs the `PriceCache`,
`TrackedSetProvider`, and provider, publishes the cache on `app.state.price_cache`, starts the
driver under `run_supervised`, and on shutdown cancels the task and then awaits `provider.aclose()`.

**The one gap left open by this pass** — `init_db()` not being called — was closed in the third
pass below. At the time of this pass the lifespan carried a marked comment where the call belonged,
and a schema-less database degraded to "prices stop updating" rather than a failed startup.

**Supporting changes:** `Settings` gained `db_path` (env var `DB_PATH`, default `db/finally.db`),
matching design §10 — the lifespan needs it to construct the `TrackedSetProvider`. `fastapi` and
`uvicorn` were added to `backend/pyproject.toml` dependencies, and `uv.lock` regenerated.

**Tests added** (all three `PLAN.md` §12 bullets that Finding #2 said could not exist yet now do):

- `backend/tests/market_data/test_sse_stream.py` (17 tests) — full snapshot on connect, including
  never-ticked `pending` tickers; the complete SSE event field set; a second connection getting its
  own independent snapshot; **unchanged tickers emitting no events**; exactly one event per real
  price change; only the changed ticker re-emitted; heartbeat present/absent per interval;
  disconnect ending the stream; SSE frame format; and the route's media type and proxy headers.
- Two of those deserve calling out because they close Finding #1's forward-looking half — the
  consequence that was predicted but not yet observable:
  `test_repeated_identical_price_emits_nothing_after_the_snapshot` (20 repeat writes produce zero
  events) and `test_massive_poll_cadence_does_not_produce_duplicate_events`, which drives 30 real
  driver cycles through a provider that keeps returning the same quote and asserts the stream stays
  silent — the exact "~30 identical events per real tick" scenario `PLAN.md` §6 names.
  `test_retracked_ticker_is_re_emitted_after_being_dropped` covers the per-connection bookkeeping
  prune, without which a re-tracked ticker's stale higher `updated_at` would suppress its events
  permanently.
- `backend/tests/market_data/test_history_endpoint.py` (9 tests) — over real HTTP via an in-process
  ASGI transport: `404` untracked, points oldest-first, `prev_close` vs `session_open` reference
  reporting, `200`/`[]` for a pending ticker, case normalization, the `HISTORY_MAXLEN` cap with
  correct eviction ends, duplicate prices not consuming buffer slots, and a dropped ticker
  reverting to `404`.
- `backend/tests/market_data/test_main.py` — route registration, injected settings, the
  pre-startup cache placeholder, the lifespan actually populating the cache and the routes reading
  that same instance, provider selection by `MASSIVE_API_KEY` through the real lifespan, shutdown
  closing both provider kinds, and no driver task surviving shutdown. (Extended in the third pass
  to 15 tests.)

### Finding #4 — CI now runs the backend suite

`.github/workflows/backend-tests.yml` runs `uv sync --locked && uv run pytest -v` in `backend/` on
pushes to `main`/`start`, on every pull request, and on manual dispatch. This closes the
"tests pass locally ≠ tests run in CI" gap noted in the last bullet of Finding #4.

The `tool.uv.dev-dependencies` deprecation warning called out in the original test-results section
was also cleared by migrating to `[dependency-groups] dev = [...]`. The suite now runs with no
warnings at all.

The first Finding #4 bullet (`TrackedSetProvider` opening a fresh connection per cycle) was
deliberately left alone — it remains the documented simplicity-over-throughput tradeoff.

## Resolution — third pass (2026-08-08) — limitations triaged and closed

The second pass closed every numbered finding but left a "Remaining Limitations" list. Each item on
it was re-examined and sorted into **in scope for Market Data Backend readiness** (implemented
here) or **genuinely out of scope** (documented and deferred, with the reason).

### In scope — implemented

#### `init_db()` is now called from the lifespan

**Verdict: in scope, and the most consequential item on the list.** The reasoning that had deferred
it — "`app/db/` belongs to the database module's design doc" — confused *who owns the queries* with
*who owns startup*. Three things settle it:

- The market data subsystem cannot function at all without `watchlist` and `positions`.
  `TrackedSetProvider` reads them on the driver's first cycle, so a fresh checkout produced an app
  that crash-looped its only price feed forever.
- `PLAN.md` §7 does not defer the schema to a later module — it specifies it completely (all six
  tables, columns, constraints, and the seed data) and requires it in the lifespan "before any
  background task is launched or any request is served."
- `market-data-design.md` §14 lists `init_db()` → `build_market_data_provider()` →
  `run_supervised(...)` as a **startup integration point of this subsystem**, and `PLAN.md` §12's
  backend test bullet "schema exists and is seeded before background tasks run — the snapshot task
  never sees a missing table" is untestable without it.

A partial schema (just the two tables market data reads) was rejected: `PLAN.md` §7 explicitly
promises "No separate migration step," and shipping half the tables would force exactly that on
whoever adds the portfolio module.

**New files:**

| File | Contents |
|---|---|
| `backend/app/db/schema.sql` | All six tables from `PLAN.md` §7 — `users_profile`, `watchlist`, `positions`, `trades`, `portfolio_snapshots`, `chat_messages` — plus the `(user_id, recorded_at)` index on snapshots. Every statement is `IF NOT EXISTS`, so re-applying it on each startup is the "no migration step" mechanism. |
| `backend/app/db/init.py` | `init_db(db_path, user_id="default")`: creates the parent directory, applies the schema, and seeds on a fresh database. |
| `backend/app/db/__init__.py` | Re-exports `init_db`, `DEFAULT_WATCHLIST`, `DEFAULT_CASH_BALANCE`. |

`await init_db(settings.db_path)` is now the first statement in `lifespan()`, before the cache,
provider, and supervised task are constructed.

Two decisions worth recording:

- **Seeding is gated on the user-profile row, not on the watchlist being empty.** A user who
  deliberately removes all ten default tickers must not have them silently restored on the next
  restart. A brand-new database (or fresh Docker volume) has no profile row and gets both the
  $10,000 balance and the default watchlist; an existing one is left exactly as the user left it.
  Asserted by `test_restart_preserves_user_changes`.
- **Seed inserts use `INSERT OR IGNORE`.** A database can legitimately hold watchlist rows without
  a profile row (an earlier partial run, a test fixture). Colliding with the `(user_id, ticker)`
  unique constraint must not take startup down.

The schema also carries the `CHECK` constraints `PLAN.md` implies but does not spell out as DDL —
`side IN ('buy','sell')` on `trades` and `role IN ('user','assistant')` on `chat_messages` — so the
enum discipline `PLAN.md` §9 requires of the LLM's structured output is enforced at the storage
layer too, not only in Pydantic.

**Tests:** `backend/tests/market_data/test_db_init.py` (13 tests) — every table present, the
snapshot index created, parent directories created, the `positions` uniqueness and `trades.side`
constraints actually enforced, $10,000 seeded, the ten `PLAN.md` tickers seeded and matching the
plan verbatim, unique row ids, running twice not duplicating anything, user changes surviving a
restart, existing rows surviving a schema re-apply, and `user_id` isolation.

`test_main.py` gained the startup-ordering coverage this unblocks:
`test_startup_creates_and_seeds_a_database_that_does_not_exist` (a path that does not exist becomes
a working, streaming app), `test_the_driver_never_sees_a_missing_table` (asserts **zero** warnings
are logged by the driver or supervisor during startup — a single one would mean the ordering
guarantee had regressed), and `test_startup_stays_alive_if_the_schema_disappears_at_runtime`, which
drops a table *after* startup and confirms the app keeps serving. That last one deliberately
preserves the resilience property the now-obsolete `test_startup_survives_a_database_with_no_tables`
used to cover, so closing the gap did not cost coverage.

#### `GET /api/health`

**Verdict: system-level, not market data — but in scope for this branch.** `PLAN.md` §8 files it
under "System," so it is not part of the market data subsystem proper. It is included here because
this branch introduced the app shell (`main.py`) that owns it, it is fully specified, it has zero
coupling to market data, and leaving it out would keep the Docker/deployment healthcheck blocked on
a five-line endpoint.

Deliberately shallow: it reports that the process is up and serving, and does **not** probe the
database or the price cache. A health check that fails while the app is still serving would turn a
transient condition the supervisor is designed to ride out into a container restart loop.

Covered by `test_health_endpoint_reports_ok` and `test_health_endpoint_is_registered`.

#### Massive base URL and snapshot path — the "unverified" claim was wrong

**Verdict: not a limitation. The previous pass's claim was incorrect and is retracted.**

The second pass carried forward "unverified against Massive's live docs" from
`market-data-design.md` §8.2, which shows a placeholder host (`https://api.massive.example/v2`) and
says "confirm against live Massive docs." That confirmation had **already been done** — in
`MASSIVE_API.md`, a later and more specific document, researched against the official open-source
client `massive-com/client-python`. `market-data-design.md` §8.2 is superseded on this point.

Verified against the client repo, per `MASSIVE_API.md` §1, §2 and §5.1:

| Implementation constant | Source of truth |
|---|---|
| `MASSIVE_BASE_URL = "https://api.massive.com"` | `massive/rest/__init__.py`'s `BASE`. Massive is the Polygon.io rebrand (2025-10-30); `api.polygon.io` is the legacy host. |
| `SNAPSHOT_PATH = "/v2/snapshot/locale/us/markets/stocks/tickers"` | `RESTClient.get_snapshot_all("stocks", ...)` in `massive/rest/snapshot.py`. |
| `Authorization: Bearer <key>` | `MASSIVE_API.md` §2 — the header, not the legacy `?apiKey=` query parameter. |
| `?tickers=A,B,C` | §5.1's request shape. |
| `lastTrade.p` → `price`, `prevDay.c` → `reference_price` (`prev_close`) | §5.1's field table, confirmed against the client's deserializer `massive/rest/models/snapshot.py`. |

The implementation already matched all five exactly; no code change was needed. What was missing
was a test proving it, so `test_massive_provider.py` gained:

- `RECORDED_SNAPSHOT_RESPONSE`, the verbatim response from the official client's own test suite
  (`test_rest/mocks/v2/snapshot/locale/us/markets/stocks/tickers/index.json`, reproduced in
  `MASSIVE_API.md` §5.1) — kept complete, including the `min`/`fmv`/`todaysChangePerc` fields this
  project ignores, so the parser is proven against the real wire shape.
- `test_base_url_matches_the_official_client` and `test_snapshot_path_matches_the_official_client`,
  which pin the two constants so a future edit cannot silently drift to the legacy host or to the
  v3 unified snapshot (whose field names differ — `session`/`last_trade`, snake_case — and which
  `MASSIVE_API.md` §5.3 warns must not be mixed with v2).
- `test_fetch_parses_the_officially_recorded_response`, plus two mix-up guards the recorded fixture
  alone cannot catch: in it `day.c` and `lastTrade.p` are coincidentally both `20.506`, so
  `test_day_close_is_not_mistaken_for_the_last_trade_price` and
  `test_bid_is_not_mistaken_for_the_last_trade_price` perturb those fields and assert the price
  still comes from `lastTrade.p`.

One genuinely unverified item remains, and it is operational rather than structural — see the
deferred list below.

### Out of scope — deferred, with reasons

#### In-memory history is by design; unchanged

`PLAN.md` §6 states it outright: the ring buffer "is in-memory only and is lost on restart — it
exists to give the detail chart shape on page load, not to be a durable time series." §8 then
specifies the endpoint's behavior on a cold start: "a fresh container returns few or no points —
that is expected and the chart simply fills in from SSE." Persisting it would contradict the plan
and duplicate `portfolio_snapshots`, which is the deliberately durable series. **No change made.**
`test_pending_ticker_returns_200_with_no_points` pins the cold-start contract so it stays a `200`
with `points: []` rather than drifting into an error.

#### Playwright E2E stays deferred until the frontend exists

`PLAN.md` §12 puts E2E in `test/` with a `docker-compose.test.yml` spinning up the app plus a
Playwright container, and every listed scenario drives the UI — "default watchlist appears,"
"prices flash green/red," "the position row disappears from both the table and the heatmap." None
can be written against a backend with no frontend. The backend behaviors they would exercise are
already covered at the unit/HTTP level here (fresh-start seeding, SSE reconnect snapshot, detail
chart backfill, a held ticker staying tracked after watchlist removal). **Correctly deferred.**

#### Massive plan-tier data freshness

`MASSIVE_API.md` §8 flags, from search-summarized sources it could not fetch directly, that the
free tier may be **end-of-day only** rather than merely rate-limited — meaning free-tier prices may
look flat intraday. This changes no code (the polling loop, parsing, and unavailable-handling are
identical whichever tier's data flows through them); it is an operational expectation to document
next to `MASSIVE_API_KEY`. That belongs with the deployment/README work, not here. Note the repo
also has no committed `.env.example` yet, which `PLAN.md` §4 calls for — the natural home for that
note.

#### `TrackedSetProvider`'s per-cycle connection

Unchanged, as in the second pass: the documented simplicity-over-throughput tradeoff
(`market-data-design.md` §5).

## Final Status Table

| # | Item | Status |
|---|---|---|
| Finding 1 | `PriceCache.update()` advancing `updated_at`/`history` on unchanged prices | **Resolved** — fixed in `cache.py`; verified by test run |
| Finding 2 | SSE stream, history endpoint, FastAPI lifespan wiring absent | **Resolved** — implemented with 37 tests |
| Finding 3 | No `aclose()` path for `MassiveProvider` | **Resolved** — `aclose()` on the base interface, called by the lifespan |
| Finding 4a | `TrackedSetProvider` opens a connection per cycle | **Deferred by design** — documented tradeoff |
| Finding 4b | `_as_finite_positive_float` guard | **No action** — noted as a strength |
| Finding 4c | Per-sector shared event roll | **No action** — noted as a strength |
| Finding 4d | No CI runs `backend/tests` | **Resolved** — `Backend Tests` workflow |
| Limitation 1 | `init_db()` not called from the lifespan | **Resolved** — `app/db/` implemented and wired |
| Limitation 2 | No `/api/health` | **Resolved** — added to the app shell (system-level, shipped with `main.py`) |
| Limitation 3 | History buffer in-memory, lost on restart | **By design** — `PLAN.md` §6/§8; unchanged |
| Limitation 4 | Massive base URL / snapshot path "unverified" | **Retracted** — already verified in `MASSIVE_API.md` against the official client; now test-pinned |
| Limitation 5 | No Playwright E2E | **Deferred** — requires the frontend (`PLAN.md` §12) |
| New | Massive free-tier data freshness | **Deferred** — operational note for README/`.env.example` |
| New | No committed `.env.example` | **Deferred** — deployment module (`PLAN.md` §4) |

## Final Test Results

```
cd backend && uv sync && uv run pytest -v
...
145 passed in 16.65s
```

Run 2026-08-08 with `uv` on Windows (Python 3.13, `uv sync --locked` clean). No warnings, no skips,
no xfails.

| Source | Tests |
|---|---|
| Original review baseline | 69 |
| Finding #1 fix (`test_cache.py`) | +6 → 75 |
| Finding #3 (`test_provider_contract.py`) | +11 |
| Finding #2 — SSE (`test_sse_stream.py`) | +17 |
| Finding #2 — history endpoint (`test_history_endpoint.py`) | +9 |
| Finding #2/#3 — app, lifespan, health (`test_main.py`) | +15 |
| Third pass — database init (`test_db_init.py`) | +13 |
| Third pass — Massive endpoint verification (`test_massive_provider.py`) | +5 |
| **Total** | **145** |

**No existing test was weakened, skipped, or removed at any point.** One test was *replaced* rather
than deleted: `test_startup_survives_a_database_with_no_tables` asserted behavior that only existed
because `init_db()` was missing. It became
`test_startup_creates_and_seeds_a_database_that_does_not_exist` (the new correct behavior), and its
resilience property was preserved and strengthened in
`test_startup_stays_alive_if_the_schema_disappears_at_runtime`, which breaks the database *after*
startup instead. The `seeded_db` fixture in `test_main.py` was simplified from a hand-rolled schema
to a bare path, because the lifespan now creates the database itself — testing the app instead of a
fixture.

### Manual end-to-end verification

The assembled stack was run under `uvicorn` against a **completely fresh, non-existent** database
path — the real first-run scenario:

- The parent directory and `finally.db` were created and seeded automatically; startup logged no
  warnings or errors at all.
- `GET /api/health` → `{"status": "ok"}`.
- `GET /api/prices/NFLX/history` returned a populated `points` array with `reference_kind:
  "session_open"` and ~500ms spacing — a chart with shape immediately, with no manual setup.
- `GET /api/stream/prices` delivered the full ten-ticker snapshot on connect, then live diffed
  updates with `direction` flipping between `up`/`down`/`flat` and the complete `PLAN.md` §6 field
  set.

## Readiness for the Next Stage

The market data subsystem delivers everything `market-data-design.md` §1 assigns it: the tracked
set, the shared cache, both interchangeable providers behind one interface, the SSE stream, the
history endpoint, and supervised background execution. A fresh clone now runs to a working,
streaming app with one command and no setup.

The integration surface other modules code against (design §14) is live and stable:

- `request.app.state.price_cache` → `cache.get(ticker)` for the trade executor's `409` rule and for
  portfolio valuation's `priced: false` fallback.
- Watchlist `POST`/`DELETE` need no notification — `TrackedSetProvider` picks changes up on the next
  driver cycle.
- The portfolio-snapshot task wires into `main.py`'s `tasks` list exactly like the driver, with the
  same `run_supervised` wrapper.
- All six tables from `PLAN.md` §7 already exist and are seeded, so the portfolio, trade, and chat
  modules can write against them without a migration or a schema change.

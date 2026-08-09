# FinAlly — Integration Contract

**Status: authoritative.** This file is the frozen interface between the six agents building
FinAlly. `PLAN.md` is the specification (the *what* and *why*); this file pins the exact shapes
so frontend, backend, database, LLM, DevOps, and test work can proceed in parallel without
waiting on each other.

**Rule: nobody changes a shape in this file unilaterally.** If you believe a shape here is
wrong, message the team lead (`SendMessage` to `main`) with the problem and the proposed
change. The lead updates this file and notifies everyone affected. Do not "fix" it locally —
somebody else is already coding against it.

---

## 1. Ownership map

Each path has exactly one owner. Do not edit files owned by another agent; message them instead.

| Path | Owner |
|---|---|
| `backend/app/db/**` (schema, init, repositories, connection) | **db-engineer** |
| `backend/app/portfolio/**`, `backend/app/api/routes/portfolio.py`, `watchlist.py` | **backend-engineer** |
| `backend/app/config.py`, `backend/app/main.py`, `backend/pyproject.toml` | **backend-engineer** |
| `backend/app/static_files.py` | **backend-engineer** |
| `backend/app/llm/**`, `backend/app/api/routes/chat.py` | **llm-engineer** |
| `backend/app/market_data/**`, `backend/app/api/routes/market_data.py` | **frozen — nobody** (already complete; see §9) |
| `backend/tests/**` | shared, but one subdirectory per owner (see §8) |
| `frontend/**` | **frontend-engineer** |
| `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.env.example`, `scripts/**` | **devops-engineer** |
| `test/**` (Playwright E2E, `docker-compose.test.yml`) | **integration-tester** |
| `planning/**` | **team lead** |

---

## 2. Global conventions

- **Single user.** `user_id` is always the literal `"default"`. Import `DEFAULT_USER_ID` from
  `app.db.init`.
- **Timestamps in the database** are ISO-8601 UTC strings with offset, e.g.
  `2026-08-08T14:03:11.482913+00:00` (`datetime.now(timezone.utc).isoformat()`).
- **Timestamps in market-data payloads** (`updated_at`, history `t`) are **float epoch
  seconds** — this is what the existing price cache emits. Do not convert.
- **Timestamps in portfolio payloads** (`recorded_at`, `executed_at`) are **ISO-8601 UTC
  strings**, matching the database.
- **`change_pct` is a fraction, not a percentage.** `0.0123` means +1.23%. The frontend
  multiplies by 100 for display. Same convention for every `*_pct` field in this document.
- **Money** is a plain JSON number (Python `float`). No string decimals, no cents-as-integers.
- **Errors** use FastAPI's default envelope: `{"detail": "<human readable message>"}` with the
  HTTP status codes given below. `detail` is user-facing — the frontend renders it verbatim in
  inline error slots, so write it for a human ("Insufficient cash: need $18,432.00, have
  $10,000.00"), not for a log.
- **Ticker normalization** is trim + uppercase. **Validation** is the regex `^[A-Z]{1,5}$`
  applied *after* normalization. One shared helper, owned by backend-engineer:
  `app.portfolio.validation.normalize_ticker(raw) -> str` (raises `ValueError`) — llm-engineer
  and every route use it. There must not be two copies of this rule.
- **Position epsilon** is `1e-6`, exported as `app.portfolio.validation.QUANTITY_EPSILON`.

---

## 3. REST API

Base URL is same-origin. Every endpoint below is under `/api`.

### 3.1 `GET /api/health` — *exists, done*

```json
{ "status": "ok" }
```

### 3.2 `GET /api/portfolio` — backend-engineer

```json
{
  "cash_balance": 8234.50,
  "total_value": 10412.33,
  "positions_value": 2177.83,
  "total_unrealized_pnl": 177.83,
  "total_unrealized_pnl_pct": 0.0177,
  "positions": [
    {
      "ticker": "AAPL",
      "quantity": 10.0,
      "avg_cost": 190.00,
      "current_price": 201.78,
      "market_value": 2017.80,
      "cost_basis": 1900.00,
      "unrealized_pnl": 117.80,
      "unrealized_pnl_pct": 0.062,
      "weight": 0.1938,
      "priced": true,
      "updated_at": "2026-08-08T14:03:11.482913+00:00"
    }
  ]
}
```

- `total_value` = `cash_balance` + `positions_value`.
- `weight` is the position's share of `total_value` (a fraction; includes cash in the
  denominator).
- **`priced: false`** when the ticker's cache status is `unavailable`, or it is untracked, or
  no tick has arrived. In that case `current_price` is `null`, `market_value` falls back to
  `quantity * avg_cost`, and `unrealized_pnl` / `unrealized_pnl_pct` are `null`. The UI renders
  `—` for those cells. Never fabricate a price.
- `positions` is sorted by `market_value` descending.
- Empty portfolio: `positions: []`, `positions_value: 0.0`, `total_unrealized_pnl: 0.0`,
  `total_unrealized_pnl_pct: 0.0`.

### 3.3 `POST /api/portfolio/trade` — backend-engineer

Request:

```json
{ "ticker": "AAPL", "quantity": 10, "side": "buy" }
```

Success `200`:

```json
{
  "trade": {
    "id": "0f0e…",
    "ticker": "AAPL",
    "side": "buy",
    "quantity": 10.0,
    "price": 201.78,
    "total": 2017.80,
    "executed_at": "2026-08-08T14:03:11.482913+00:00"
  },
  "cash_balance": 6216.70,
  "position": {
    "ticker": "AAPL", "quantity": 20.0, "avg_cost": 195.89,
    "updated_at": "2026-08-08T14:03:11.482913+00:00"
  }
}
```

`position` is `null` when the sell closed the position (row deleted per the epsilon rule).

**Validation order is normative** (`PLAN.md` §8). Evaluate top to bottom and return on first
failure:

| # | Condition | Status | `detail` example |
|---|---|---|---|
| 1 | `quantity` missing, non-numeric, `<= 0`, NaN, or infinite | `400` | `Invalid quantity: must be a positive number` |
| 2 | `side` not exactly `"buy"` or `"sell"` | `400` | `Invalid side: must be 'buy' or 'sell'` |
| 3 | ticker fails `^[A-Z]{1,5}$` after normalization | `400` | `Invalid ticker: 'ABCDEF'` |
| 4 | not tracked, or cache `status` is `unavailable`, or `price is None` | `409` | `No price available for ABCD` |
| 5 | buy and `quantity * price > cash_balance` | `422` | `Insufficient cash: need $18,432.00, have $10,000.00` |
| 6 | sell and `quantity > held + 1e-6` | `422` | `Insufficient shares: tried to sell 100, hold 10` |
| — | otherwise | `200` | — |

A rejected trade writes **nothing**: no `trades` row, no `positions` change, no cash change, no
snapshot.

A successful trade is **one atomic transaction**: insert `trades`, upsert-or-delete `positions`,
update `users_profile.cash_balance`, insert `portfolio_snapshots`. All four or none.

Buy math: `new_avg_cost = (old_qty * old_avg_cost + qty * price) / (old_qty + qty)`.
Sell math: `avg_cost` is unchanged; `cash += qty * price`. When
`old_qty - qty < 1e-6` the sell quantity is raised to exactly `old_qty` (absorbing float
residue) and the row is deleted.

### 3.4 `GET /api/portfolio/history` — backend-engineer

Query params: `since` (ISO-8601, default `now - 24h`), `limit` (default `500`, max `2000`).

```json
{
  "since": "2026-08-07T14:03:11+00:00",
  "limit": 500,
  "points": [
    { "recorded_at": "2026-08-07T14:03:11.482913+00:00", "total_value": 10000.0 }
  ]
}
```

Oldest first. `limit` out of range → `400`. Unparseable `since` → `400`. When the range holds
more rows than `limit`, **downsample evenly across the range, preserving the first and last
row** — do not truncate.

**Do not pass the request's `limit` into `list_snapshots`.** That repo function applies `LIMIT`
in SQL and therefore returns the **oldest** `limit` rows in the window — pass `limit=500` and
you get the first 500 points of the 24-hour window, with nothing left for the downsampler to
thin. The chart would then silently show only the beginning of the window on any container up
longer than a few hours, and look perfectly plausible while doing it. Correct usage: fetch with
a cap well above the display count (24h at a 30s cadence is ~2,880 rows), then downsample to
the request's `limit` in the API layer.

### 3.5 `GET /api/watchlist` — backend-engineer

```json
{
  "tickers": [
    {
      "ticker": "AAPL",
      "added_at": "2026-08-08T14:00:00+00:00",
      "price": 201.78,
      "prev_price": 201.60,
      "reference_price": 199.10,
      "reference_kind": "session_open",
      "change_pct": 0.01346,
      "direction": "up",
      "status": "ok",
      "updated_at": 1786000991.482
    }
  ]
}
```

The per-ticker market-data fields are exactly `CacheEntry.to_sse_event()` merged into the row,
so the frontend can reuse one renderer for both the REST payload and SSE events. A ticker with
no cache entry reports `status: "pending"` and `null` for every price field. Sorted by
`added_at` ascending.

### 3.6 `POST /api/watchlist` — backend-engineer

Request `{ "ticker": "pypl" }`. Response `200` with the same object shape as one element of
`tickers` above. Adding a ticker already present is **idempotent**: `200`, no duplicate row,
original `added_at` preserved. Invalid format → `400`.

### 3.7 `DELETE /api/watchlist/{ticker}` — backend-engineer

`204` with empty body, including when the ticker was not on the list (idempotent). Invalid
format → `400`. **Removes the watchlist row only** — never touches `positions`, and a held
ticker stays in the tracked set through its open position.

### 3.8 `POST /api/chat` — llm-engineer

Request:

```json
{ "message": "buy 100 NVDA" }
```

Response `200`:

```json
{
  "message": "Placing an order to buy 100 NVDA to lift your tech weighting.",
  "actions": [
    {
      "kind": "trade",
      "status": "failed",
      "detail": { "ticker": "NVDA", "side": "buy", "quantity": 100 },
      "error": "Insufficient cash: need $18,432.00, have $10,000.00"
    }
  ],
  "created_at": "2026-08-08T14:03:11.482913+00:00"
}
```

- `kind`: `"trade"` | `"watchlist_change"`.
- `status`: `"applied"` | `"failed"`.
- `detail` for an **applied trade** additionally carries `fill_price` and `total`:
  `{"ticker":"AAPL","side":"buy","quantity":10,"fill_price":201.78,"total":2017.80}`.
- `detail` for a **watchlist change** is `{"ticker":"PYPL","action":"add"}`.
- `error` is present **only** when `status` is `"failed"`, and is the `detail` string from the
  underlying validation — same text the manual trade bar would show.
- `actions` is `[]` when the model requested nothing. It is never `null`.
- `not settings.chat_enabled` (i.e. no `OPENROUTER_API_KEY` and `LLM_MOCK` not true) → **`503`**
  with `{"detail": "Chat is unavailable: OPENROUTER_API_KEY is not configured"}`. This must not
  affect any other endpoint or prevent startup.
- Upstream LLM error or unparseable response → `502` with a human-readable `detail`.

### 3.9 `GET /api/chat/history` — llm-engineer

Added so the chat panel survives a page reload (`PLAN.md` §9 requires the stored `actions` to
outlive a refresh).

```json
{
  "messages": [
    { "id": "…", "role": "user", "content": "buy 100 NVDA", "actions": null,
      "created_at": "2026-08-08T14:03:10+00:00" },
    { "id": "…", "role": "assistant", "content": "Placing an order…",
      "actions": [ … same shape as §3.8 … ],
      "created_at": "2026-08-08T14:03:11+00:00" }
  ]
}
```

Oldest first, `?limit=` default `50`, max `200`.

### 3.10 Market data — *exists, done, frozen*

`GET /api/stream/prices` (SSE) and `GET /api/prices/{ticker}/history`. See §9.

---

## 4. Database access layer — db-engineer

Everything that touches SQLite goes through this layer. **No route handler and no service
opens a connection or writes SQL directly.** Module: `backend/app/db/`.

### 4.1 Connection

```python
# app/db/connection.py
@asynccontextmanager
async def connect(db_path: str) -> AsyncIterator[aiosqlite.Connection]: ...
```

Sets `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`, `row_factory = aiosqlite.Row`, and
`PRAGMA busy_timeout=5000` (the snapshot task and a request can contend).

```python
@asynccontextmanager
async def transaction(db: aiosqlite.Connection) -> AsyncIterator[aiosqlite.Connection]: ...
```

Commits on clean exit, rolls back on exception. The trade path uses this.

### 4.2 Repository functions

All take an `aiosqlite.Connection` as their first argument so callers can compose several
inside one `transaction()`. All default `user_id` to `DEFAULT_USER_ID`.

```python
# app/db/watchlist_repo.py
async def list_watchlist(db, user_id=...) -> list[WatchlistRow]        # ordered by added_at
async def add_watchlist_ticker(db, ticker, user_id=...) -> WatchlistRow # idempotent
async def remove_watchlist_ticker(db, ticker, user_id=...) -> bool      # True if a row went

# app/db/positions_repo.py
async def list_positions(db, user_id=...) -> list[PositionRow]
async def get_position(db, ticker, user_id=...) -> PositionRow | None
async def upsert_position(db, ticker, quantity, avg_cost, user_id=...) -> PositionRow
async def delete_position(db, ticker, user_id=...) -> None

# app/db/trades_repo.py
async def insert_trade(db, ticker, side, quantity, price, user_id=...) -> TradeRow
async def list_trades(db, limit=100, user_id=...) -> list[TradeRow]     # newest first

# app/db/profile_repo.py
async def get_cash_balance(db, user_id=...) -> float
async def set_cash_balance(db, value, user_id=...) -> None

# app/db/snapshots_repo.py
async def insert_snapshot(db, total_value, user_id=...) -> SnapshotRow
async def list_snapshots(db, since_iso, limit, user_id=...) -> list[SnapshotRow]
async def prune_snapshots(db, older_than_iso, user_id=...) -> int       # rows deleted

# app/db/chat_repo.py
async def insert_chat_message(db, role, content, actions, user_id=...) -> ChatRow
async def list_chat_messages(db, limit=50, user_id=...) -> list[ChatRow]  # oldest first
```

`actions` is passed as a Python object (`list | None`) and JSON-encoded inside the repo; it is
JSON-decoded on read. Callers never see a JSON string.

**Repositories never commit.** Every write must be wrapped in `async with transaction(db)` —
`connect()` rolls back as it closes, so an unwrapped write is silently lost rather than raising.

`profile_repo` raises `ProfileMissingError` (a `LookupError`) when there is no `users_profile`
row, rather than defaulting the balance to `0.0` or no-opping the UPDATE. `init_db` always
seeds that row, so this fires only on a genuinely broken database or a fixture that skipped
initialization — in both cases loudly, which is the point.

Row types are frozen dataclasses in `app/db/rows.py` with the column names as fields, plus
`to_dict()` returning JSON-ready values. `list_snapshots` applies `since`/`limit` in SQL;
**downsampling is the API layer's job, not the repo's.**

### 4.3 Existing, do not break

`app/db/schema.sql` and `app/db/init.py:init_db` already exist and are tested. Extend, don't
rewrite. `DEFAULT_USER_ID` and `DEFAULT_CASH_BALANCE` live in `init.py`.

---

## 5. Backend services — backend-engineer

```python
# app/portfolio/validation.py
QUANTITY_EPSILON = 1e-6
def normalize_ticker(raw: str) -> str        # trim/upper/validate, raises ValueError
def validate_quantity(raw) -> float          # raises ValueError
def validate_side(raw) -> Literal["buy","sell"]

# app/portfolio/service.py  — the single trade code path (manual AND LLM)
class TradeError(Exception):
    status_code: int      # 400 | 409 | 422
    detail: str

async def execute_trade(db_path, cache, ticker, quantity, side, user_id=...) -> dict
    """Runs the §3.3 validation ladder, then the atomic transaction.
    Raises TradeError on any rejection. Returns the §3.3 success body."""

async def build_portfolio(db_path, cache, user_id=...) -> dict   # the §3.2 body
async def compute_total_value(db_path, cache, user_id=...) -> float
```

```python
# app/portfolio/watchlist_service.py
class WatchlistError(Exception):
    status_code: int
    detail: str

async def get_watchlist(db_path, cache, user_id=...) -> dict     # the §3.5 body
async def add_ticker(db_path, cache, ticker, user_id=...) -> dict
async def remove_ticker(db_path, ticker, user_id=...) -> None
```

**llm-engineer calls these service functions directly** — not the HTTP endpoints — and maps
`TradeError` / `WatchlistError` onto the `actions` array. One validation ladder, two entry
points. This is the single most important integration point in the project: if the chat path
grows its own copy of the rules, they will drift.

### 5.1 Snapshot background task — backend-engineer

`app/portfolio/snapshot_task.py:run_snapshot_loop(db_path, cache)` — every 30 s: compute total
value, insert a `portfolio_snapshots` row, prune rows older than 30 days. Wrapped in the
existing `app.market_data.supervisor.run_supervised` so a raise restarts it with backoff. The
interval is a module constant so tests can shrink it.

### 5.2 Settings — backend-engineer

Extend the existing frozen dataclass in `app/config.py` (do not replace it):

| Field | Env var | Default |
|---|---|---|
| `massive_api_key` | `MASSIVE_API_KEY` | `""` *(exists)* |
| `massive_poll_interval_seconds` | `MASSIVE_POLL_INTERVAL_SECONDS` | `15.0` *(exists)* |
| `db_path` | `DB_PATH` | `"db/finally.db"` *(exists)* |
| `openrouter_api_key` | `OPENROUTER_API_KEY` | `""` |
| `llm_mock` | `LLM_MOCK` | `False` (true iff the value lowercases to `"true"`/`"1"`) |
| `llm_model` | `LLM_MODEL` | `"openrouter/openai/gpt-oss-120b"` |
| `static_dir` | `STATIC_DIR` | `"static"` |

`Settings.from_env()` also loads a project-root `.env` **if present** (host dev runs), searching
cwd → repo root → `backend/` and loading with `override=False` so the **process environment
always wins** and Docker stays authoritative. A missing `.env` is never an error.

**`Settings.chat_enabled`** (property) → `bool(openrouter_api_key) or llm_mock`. This is the
single predicate for the §3.8 `503` decision — mock mode is a complete substitute for the key,
so a mock-mode container with no API key serves chat normally.

### 5.3 Static file serving — backend-engineer

`app/static_files.py:mount_static(app, static_dir)`. Mounts the Next.js export. Requirements:

- Must be registered **after** every `/api` router so it never shadows an API route.
- Missing `static_dir` (local backend-only dev) logs a warning and is a **no-op**, not a crash.
- Serves `index.html` for `/`, and falls back to `404.html`/`index.html` for unknown non-`/api`
  paths so a client-side route reload works.
- Next.js `output: 'export'` emits `foo.html` for route `/foo` — resolve extensionless paths to
  `<path>.html` and `<path>/index.html` before falling back.

---

## 6. LLM integration — llm-engineer

Module `app/llm/`. Use the **`cerebras` skill** (`Skill` tool, name `cerebras`) for the LiteLLM
call — do not write the provider-routing arguments from memory.

Pydantic models backing the structured output. The enums and the `gt=0` bound must be
**declared in the model**, not merely described in the prompt:

```python
class TradeIntent(BaseModel):
    ticker: str = Field(pattern=r"^[A-Za-z]{1,5}$")
    side: Literal["buy", "sell"]
    quantity: float = Field(gt=0)

class WatchlistChange(BaseModel):
    ticker: str = Field(pattern=r"^[A-Za-z]{1,5}$")
    action: Literal["add", "remove"]

class LLMResponse(BaseModel):
    message: str
    trades: list[TradeIntent] = []
    watchlist_changes: list[WatchlistChange] = []
```

**Case tolerance on the model's side only.** Attach a Pydantic `BeforeValidator` to `side`,
`action` and `ticker` that strips and lowercases (uppercases, for `ticker`) a string input
before the `Literal` / pattern check. The JSON schema sent to the model still declares the
strict enum — PLAN.md §9's requirement is unchanged — but a model that emits `"Buy"` then
degrades into one ordinary action rather than failing `LLMResponse` validation and taking the
**entire response, message included, to a `502`**. A capitalization slip must not cost the user
their reply.

This tolerance stops at the schema boundary. `validate_side` in `app/portfolio/validation.py`
is **strict and does not case-fold** (`"BUY"` → `400`), per PLAN.md §8's "not exactly". Both of
its producers are machines — UI buttons and a `Literal`-typed structured output — so a
near-miss arriving there is a bug worth surfacing, not a typo worth absorbing.

Flow per user message (exactly **one** LLM call): load portfolio context via
`build_portfolio` + `get_watchlist` → load recent history via `list_chat_messages` → build
prompt → call → parse → execute actions **in order, one failure does not abort the rest**,
each through `execute_trade` / `add_ticker` / `remove_ticker` → persist the user row and the
assistant row (with `actions`) → return §3.8.

The stored assistant `actions` must be fed back into the next turn's history so the model can
see that a trade failed.

### 6.1 Mock mode

`LLM_MOCK=true` replaces **only** the LLM call; execution and result reporting run for real.
Keyed off substrings of the user's message, case-insensitive. The E2E suite depends on these
keys — **integration-tester and llm-engineer must keep this table in sync, and it may not
change without updating both sides:**

| Trigger substring | Mock behaviour |
|---|---|
| `buy 100 nvda` | message claims the buy is placed; `trades: [{NVDA, buy, 100}]` → **must fail** on insufficient cash, exercising the failed-action UI |
| `buy 1 aapl` | message + `trades: [{AAPL, buy, 1}]` → succeeds |
| `sell all` | message + `trades: [{AAPL, sell, 100000}]` → fails on insufficient shares. The quantity is pinned to the literal `100000` so the resulting `detail` string is deterministic and the E2E suite can assert its text |
| `add pypl` | `watchlist_changes: [{PYPL, add}]` → succeeds |
| `remove nflx` | `watchlist_changes: [{NFLX, remove}]` → succeeds |
| `buy zzzz` | `trades: [{ZZZZ, buy, 1}]` → fails `409` no price available |
| anything else | analysis-style prose, `actions: []` |

---

## 7. Frontend — frontend-engineer

- Next.js + TypeScript, `output: 'export'`, `images: { unoptimized: true }`, `trailingSlash`
  left at the default. Build output is `frontend/out/`; DevOps copies that into the image.
- Tailwind, dark theme. Background `#0d1117`, panel `#161b22`, muted gray borders, **no pure
  black**. Accent yellow `#ecad0a`, blue primary `#209dd7`, purple secondary `#753991` (submit
  buttons). Up = green, down = red.
- SSE via native `EventSource` on `/api/stream/prices`. Every event is the §3.5 market-data
  shape. Reconnect is automatic and every reconnect re-sends a full snapshot — **do not write
  catch-up logic.** Connection status dot: green connected / yellow reconnecting / red
  disconnected, driven by `EventSource.readyState` and its `onerror`.
- `change_pct` is a fraction — multiply by 100. Column header is **Change %** with the baseline
  disclosed from `reference_kind` (`prev_close` → "since previous close", `session_open` →
  "since session open"). There is no "daily change" anywhere.
- Any ticker whose `status` is not `"ok"` renders `—`, never `0` and never a stale price.
- Sparklines accumulate from SSE since page load (**no** history fetch). The **main detail
  chart** seeds from `GET /api/prices/{ticker}/history` on selection, then appends SSE points.
- Chat: render `actions` beneath each assistant message. Failed actions are visually distinct
  (red border/left rule) and show `error`. **The action list is authoritative even when it
  contradicts the prose above it** — that is the point of the feature.
- All requests are same-origin relative paths (`/api/...`). No env-var base URL, no CORS.

### 7.1 Test hooks (required by the E2E suite)

integration-tester selects on these. They are part of the contract.

**The `data-value` rule.** Every element below that displays a number carries a
`data-value` attribute holding the **raw, unformatted** number (`data-value="8234.5"`, not
`"$8,234.50"`). Tests assert on `data-value`; humans read the formatted text. This decouples
the suite from currency/locale formatting, which frontend-engineer owns and may change freely.

**The unpriced rule.** When a value is unavailable (`priced: false`, or a cache `status` other
than `ok`), the element renders `—` **and omits `data-value` entirely**. An absent attribute is
how "no price" is asserted, and it can never be confused with a real `0`.

| `data-testid` | Element | Extra attributes |
|---|---|---|
| `connection-status` | the status dot | `data-status="connected\|reconnecting\|disconnected"` |
| `cash-balance` | header cash figure | `data-value` |
| `total-value` | header portfolio total | `data-value` |
| `watchlist` | watchlist container | |
| `watchlist-row-{TICKER}` | one watchlist row | |
| `watchlist-price-{TICKER}` | that row's price cell | `data-value` |
| `watchlist-add-input` / `watchlist-add-submit` | add-ticker form | |
| `watchlist-remove-{TICKER}` | remove button | |
| `detail-chart` | main chart container | `data-ticker="{TICKER}"`, `data-point-count="{n}"` |
| `positions-table` | positions table | |
| `position-row-{TICKER}` | one position row | |
| `position-qty-{TICKER}` | quantity cell | `data-value` |
| `position-avg-cost-{TICKER}` | average cost cell | `data-value` |
| `position-price-{TICKER}` | current price cell | `data-value` (omitted when unpriced) |
| `position-pnl-{TICKER}` | unrealized P&L cell | `data-value` (omitted when unpriced) |
| `heatmap` | portfolio treemap | |
| `heatmap-tile-{TICKER}` | one tile | `data-pnl-sign="up\|down\|flat"`, `data-weight` |
| `pnl-chart` | P&L chart container | `data-point-count="{n}"` |
| `trade-ticker` / `trade-quantity` / `trade-buy` / `trade-sell` | trade bar | |
| `trade-error` | inline trade rejection message | absent from the DOM when there is no error |
| `chat-input` / `chat-send` / `chat-messages` | chat panel | |
| `chat-message-{n}` | nth message in the conversation, 0-based | `data-role="user\|assistant"` |
| `chat-action-{m}-{a}` | action `a` of message `m`, rendered **inside** `chat-message-{m}` | `data-status="applied\|failed"` |
| `chat-action-error-{m}-{a}` | the error string of a failed action | present only when that action failed |
| `chat-loading` | loading indicator | present only while awaiting a response |

`data-point-count` on both charts must update whenever the series updates. It is the only way
to assert "the chart has history immediately after a reload" — a canvas is opaque to the E2E
suite, so a chart that renders an empty container and one that renders a seeded series are
otherwise indistinguishable.

### 7.2 Selected ticker survives a reload

The selected ticker is persisted in the **URL hash** (`/#AAPL`). On mount, read the hash: if it
is a valid ticker, select it; otherwise select the first watchlist entry. Clicking a watchlist
row updates the hash.

This is a product requirement, not only a test affordance — PLAN.md §12 asks the detail chart
to have history "immediately rather than starting empty" *after a reload*, which is only
meaningful if the reload returns to the same ticker. It also makes a view shareable.

---

## 8. Testing

- **Backend unit tests** live in `backend/tests/<area>/`: `db/` (db-engineer),
  `portfolio/` (backend-engineer), `llm/` (llm-engineer). `market_data/` is existing and
  frozen. Every agent keeps `uv run pytest` green for the whole suite, not just their folder.
- **Test module basenames must be globally unique across `backend/tests/**`.** There are no
  `__init__.py` files there, so pytest imports by basename and two `test_service.py` files in
  different subdirectories collide with an import error that looks nothing like its cause.
  Prefix with your area: `test_portfolio_service.py`, `test_llm_client.py`, `test_db_trades.py`.
- **Frontend unit tests** in `frontend/` per Vitest + React Testing Library convention.
- **E2E** in `test/`, Playwright, `LLM_MOCK=true`, driving the built container.
- Each engineer writes tests for their own code as they go. integration-tester does not write
  unit tests for other people's modules — it owns cross-boundary and browser-level coverage.

---

## 9. Frozen: the market data subsystem

`backend/app/market_data/**` and `backend/app/api/routes/market_data.py` are complete, reviewed
and tested. **Read them; do not modify them.** If you need a change there, message the lead.

What you need to know to consume it:

- The shared `PriceCache` instance is on `app.state.price_cache`, created in the lifespan in
  `app/main.py`. Services take the cache as a parameter — do not reach into `app.state` from
  inside a service function.
- `cache.get(ticker) -> CacheEntry | None`; `cache.snapshot() -> list[CacheEntry]`;
  `cache.tracked_tickers() -> set[str]`.
- `CacheEntry` fields: `ticker, price, prev_price, reference_price, reference_kind,
  updated_at (float epoch), status, history (deque[PricePoint])`, plus properties
  `change_pct` (fraction | None) and `direction` (`up`/`down`/`flat`), and
  `to_sse_event() -> dict`.
- `TickerStatus` is `pending` | `ok` | `unavailable`. **A price is usable for a trade only when
  `status is OK and price is not None`.**
- The tracked set is recomputed from the database every driver cycle by
  `TrackedSetProvider`, as `watchlist ∪ open positions`. Adding a watchlist row or opening a
  position is therefore all it takes to start pricing a ticker — there is no register call.
- `app.market_data.supervisor.run_supervised(name, factory)` is the restart-with-backoff
  wrapper for background tasks. Use it for the snapshot task.

---

## 10. Wiring order in `main.py` — backend-engineer, last

Routers in this order, then static last:

```
market_data.router  (exists)  →  portfolio.router  →  watchlist.router  →  chat.router
→ mount_static(app, settings.static_dir)
```

Lifespan order stays: `init_db` → cache/provider construction → supervised background tasks
(market data **and** snapshots) → serve. Shutdown cancels tasks, then `provider.aclose()`.

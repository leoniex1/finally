# FinAlly — AI Trading Workstation

## Project Specification

## 1. Vision

FinAlly (Finance Ally) is a visually stunning AI-powered trading workstation that streams live market data, lets users trade a simulated portfolio, and integrates an LLM chat assistant that can analyze positions and execute trades on the user's behalf. It looks and feels like a modern Bloomberg terminal with an AI copilot.

This is the capstone project for an agentic AI coding course. It is built entirely by Coding Agents demonstrating how orchestrated AI agents can produce a production-quality full-stack application. Agents interact through files in `planning/`.

## 2. User Experience

### First Launch

The user runs a single Docker command (or a provided start script). A browser opens to `http://localhost:8000`. No login, no signup. They immediately see:

- A watchlist of 10 default tickers with live-updating prices in a grid
- $10,000 in virtual cash
- A dark, data-rich trading terminal aesthetic
- An AI chat panel ready to assist

### What the User Can Do

- **Watch prices stream** — prices flash green (uptick) or red (downtick) with subtle CSS animations that fade
- **View sparkline mini-charts** — price action beside each ticker in the watchlist, accumulated on the frontend from the SSE stream since page load (sparklines fill in progressively)
- **Click a ticker** to see a larger detailed chart in the main chart area, backfilled from the server's recent price history so it has shape immediately on page load
- **Buy and sell shares** — market orders only, instant fill at current price, no fees, no confirmation dialog
- **Monitor their portfolio** — a heatmap (treemap) showing positions sized by weight and colored by P&L, plus a P&L chart tracking total portfolio value over time
- **View a positions table** — ticker, quantity, average cost, current price, unrealized P&L, % change
- **Chat with the AI assistant** — ask about their portfolio, get analysis, and have the AI execute trades and manage the watchlist through natural language
- **Manage the watchlist** — add/remove tickers manually or via the AI chat

### Visual Design

- **Dark theme**: backgrounds around `#0d1117` or `#1a1a2e`, muted gray borders, no pure black
- **Price flash animations**: brief green/red background highlight on price change, fading over ~500ms via CSS transitions
- **Connection status indicator**: a small colored dot (green = connected, yellow = reconnecting, red = disconnected) visible in the header
- **Professional, data-dense layout**: inspired by Bloomberg/trading terminals — every pixel earns its place
- **Responsive but desktop-first**: optimized for wide screens, functional on tablet

### Color Scheme
- Accent Yellow: `#ecad0a`
- Blue Primary: `#209dd7`
- Purple Secondary: `#753991` (submit buttons)

## 3. Architecture Overview

### Single Container, Single Port

```
┌─────────────────────────────────────────────────┐
│  Docker Container (port 8000)                   │
│                                                 │
│  FastAPI (Python/uv)                            │
│  ├── /api/*          REST endpoints             │
│  ├── /api/stream/*   SSE streaming              │
│  └── /*              Static file serving         │
│                      (Next.js export)            │
│                                                 │
│  SQLite database (volume-mounted)               │
│  Background task: market data polling/sim        │
└─────────────────────────────────────────────────┘
```

- **Frontend**: Next.js with TypeScript, built as a static export (`output: 'export'`), served by FastAPI as static files
- **Backend**: FastAPI (Python), managed as a `uv` project
- **Database**: SQLite, single file at `db/finally.db`, volume-mounted for persistence
- **Real-time data**: Server-Sent Events (SSE) — simpler than WebSockets, one-way server→client push, works everywhere
- **AI integration**: LiteLLM → OpenRouter (Cerebras for fast inference), with structured outputs for trade execution
- **Market data**: Environment-variable driven — simulator by default, real data via Massive API if key provided

### Why These Choices

| Decision | Rationale |
|---|---|
| SSE over WebSockets | One-way push is all we need; simpler, no bidirectional complexity, universal browser support |
| Static Next.js export | Single origin, no CORS issues, one port, one container, simple deployment |
| SQLite over Postgres | No auth = a single active user = no need for a database server; self-contained, zero config. The `user_id` columns keep the schema multi-user-ready without paying for it now. |
| Single Docker container | Students run one command; no docker-compose for production, no service orchestration |
| uv for Python | Fast, modern Python project management; reproducible lockfile; what students should learn |
| Market orders only | Eliminates order book, limit order logic, partial fills — dramatically simpler portfolio math |

---

## 4. Directory Structure

```
finally/
├── frontend/                 # Next.js TypeScript project (static export)
├── backend/                  # FastAPI uv project (Python)
│   └── db/                   # Schema definitions, seed data, migration logic
├── planning/                 # Project-wide documentation for agents
│   ├── PLAN.md               # This document
│   └── ...                   # Additional agent reference docs
├── scripts/
│   ├── start_mac.sh          # Launch Docker container (macOS/Linux)
│   ├── stop_mac.sh           # Stop Docker container (macOS/Linux)
│   ├── start_windows.ps1     # Launch Docker container (Windows PowerShell)
│   └── stop_windows.ps1      # Stop Docker container (Windows PowerShell)
├── test/                     # Playwright E2E tests + docker-compose.test.yml
├── db/                       # SQLite location for local (non-Docker) dev runs
│   └── .gitkeep              # Directory exists in repo; finally.db is gitignored
├── Dockerfile                # Multi-stage build (Node → Python)
├── docker-compose.yml        # Optional convenience wrapper
├── .env                      # Environment variables (gitignored, .env.example committed)
└── .gitignore
```

### Key Boundaries

- **`frontend/`** is a self-contained Next.js project. It knows nothing about Python. It talks to the backend via `/api/*` endpoints and `/api/stream/*` SSE endpoints. Internal structure is up to the Frontend Engineer agent.
- **`backend/`** is a self-contained uv project with its own `pyproject.toml`. It owns all server logic including database initialization, schema, seed data, API routes, SSE streaming, market data, and LLM integration. Internal structure is up to the Backend/Market Data agents.
- **`backend/db/`** contains schema SQL definitions and seed logic. The backend initializes the database during application startup — creating tables and seeding default data if the SQLite file doesn't exist or is empty — before any background task or request handler runs. See §7.
- **`db/`** at the top level is where the SQLite file lands when the backend runs directly on the host (local development). In Docker, the database lives at `/app/db` inside the container, backed by the named volume `finally-data`; the repo's `db/` directory is not involved. See §11.
- **`planning/`** contains project-wide documentation, including this plan. All agents reference files here as the shared contract.
- **`test/`** contains Playwright E2E tests and supporting infrastructure (e.g., `docker-compose.test.yml`). Unit tests live within `frontend/` and `backend/` respectively, following each framework's conventions.
- **`scripts/`** contains start/stop scripts that wrap Docker commands.

---

## 5. Environment Variables

```bash
# Required: OpenRouter API key for LLM chat functionality
OPENROUTER_API_KEY=your-openrouter-api-key-here

# Optional: Massive (Polygon.io) API key for real market data
# If not set, the built-in market simulator is used (recommended for most users)
MASSIVE_API_KEY=

# Optional: Set to "true" for deterministic mock LLM responses (testing)
LLM_MOCK=false
```

### Behavior

- If `MASSIVE_API_KEY` is set and non-empty → backend uses Massive REST API for market data
- If `MASSIVE_API_KEY` is absent or empty → backend uses the built-in market simulator
- If `LLM_MOCK=true` → backend returns deterministic mock LLM responses (for E2E tests)
- **Configuration is always read from the process environment.** In Docker, values arrive via `--env-file .env` (or `env_file:` in compose) — there is no `.env` file inside the container. For local host runs the backend additionally loads a `.env` from the project root *if one is present*; a missing `.env` is never an error.
- A missing `OPENROUTER_API_KEY` disables chat only: `/api/chat` returns a clear error and the rest of the app works normally. It must not prevent startup.

---

## 6. Market Data

### Two Implementations, One Interface

Both the simulator and the Massive client implement the same abstract interface. The backend selects which to use based on the environment variable. All downstream code (SSE streaming, price cache, frontend) is agnostic to the source.

### The Tracked Set

Every consumer of market data — the simulator, the Massive poller, the price cache, and the SSE stream — operates on one shared definition:

> **tracked = watchlist tickers ∪ tickers with an open position**

A ticker you hold is tracked whether or not it appears in the watchlist. This matters because removing a held ticker from the watchlist must never stop its price updates: portfolio value, unrealized P&L, and `portfolio_snapshots` all depend on a live price for every open position. The tracked set is recomputed whenever the watchlist changes or a trade opens/closes a position.

### Simulator (Default)

- Generates prices using geometric Brownian motion (GBM) with configurable drift and volatility per ticker
- Updates at ~500ms intervals
- Correlated moves across tickers (e.g., tech stocks move together)
- Occasional random "events" — sudden 2-5% moves on a ticker for drama
- Starts from realistic seed prices (e.g., AAPL ~$190, GOOGL ~$175, etc.) for a table of well-known symbols
- **Unknown tickers**: any tracked ticker without a table entry gets a deterministic seed price derived from a hash of its symbol into a plausible range (roughly $20–$400), with default drift and volatility. This is deterministic across restarts, so a given symbol always starts at the same price. The simulator begins ticking a newly tracked ticker on its next cycle (~500ms), so a freshly added ticker is priced almost immediately.
- Runs as an in-process background task — no external dependencies

### Massive API (Optional)

- REST API polling (not WebSocket) — simpler, works on all tiers
- Polls for the tracked set (see above) on a configurable interval
- Free tier (5 calls/min): poll every 15 seconds
- Paid tiers: poll every 2-15 seconds depending on tier
- Parses REST response into the same format as the simulator
- **Unknown tickers**: if the API returns no data for a tracked ticker, the cache marks it `status: "unavailable"` rather than inventing a price (a ticker awaiting its first poll is `"pending"`). It stays tracked and is retried on subsequent polls (real symbols can be missing from a single response). The UI shows `—` for its price and trading is refused (see §8, Trade Execution Rules).

### Shared Price Cache

A single background task (simulator or Massive poller) writes to an in-memory price cache. For each tracked ticker the cache holds:

| Field | Meaning |
|---|---|
| `price` | Latest price |
| `prev_price` | Price at the previous update, for tick direction |
| `reference_price` | Baseline for the change % shown in the UI (see below) |
| `reference_kind` | `"prev_close"` or `"session_open"` — what the baseline actually is |
| `updated_at` | Timestamp of the last *change* to `price` |
| `status` | `"ok"`, `"pending"` (tracked, no tick yet), or `"unavailable"` (source has no data) |
| `history` | Bounded deque of `(timestamp, price)` points |

**`reference_price` / `reference_kind`** exist so the UI can show an honest change %. Massive responses carry a previous close — when present, that is the reference and `reference_kind` is `"prev_close"`. Otherwise (always, for the simulator) the reference is the first price this process observed for the ticker and `reference_kind` is `"session_open"`. There is deliberately no "daily change %" anywhere in the system: nothing persists a real trading-day close, so the UI labels this column **Change %** and discloses the baseline (see §10).

**`history`** is a per-ticker ring buffer of recent points, appended only when the price actually changes, capped at ~2,000 points per ticker (order of an hour of simulator ticks). It is in-memory only and is lost on restart — it exists to give the detail chart shape on page load, not to be a durable time series. It is served by `GET /api/prices/{ticker}/history` (§8).

SSE streams read from this cache and push updates to connected clients. This architecture supports future multi-user scenarios without changes to the data layer.

### SSE Streaming

- Endpoint: `GET /api/stream/prices`
- Long-lived SSE connection; client uses native `EventSource` API
- **On connect**, the server sends one event per tracked ticker carrying the current cache state, so a fresh client is fully populated without waiting for a tick
- **Thereafter the server emits a ticker's event only when that ticker's `updated_at` has advanced since the last event sent on that connection.** The stream is checked at ~500ms, but unchanged tickers produce no traffic. This matters for Massive: at a 15-second poll a naive fixed-cadence stream would emit ~30 identical events per real tick, each with `prev_price == price`, which would suppress the flash animation and pack the frontend sparklines with duplicate points.
- A comment heartbeat (`: ping`) is sent every 15 seconds so idle connections and intermediate proxies stay open
- Each SSE event contains: `ticker`, `price`, `prev_price`, `reference_price`, `reference_kind`, `change_pct`, `direction` (`up`/`down`/`flat`), `status`, `updated_at`
- Client handles reconnection automatically (EventSource has built-in retry); because every reconnect begins with a full snapshot, no client-side catch-up logic is needed

---

## 7. Database

### SQLite with Startup Initialization

The backend initializes the database **during application startup, in the FastAPI lifespan handler, before any background task is launched or any request is served.** If the file doesn't exist or tables are missing, it creates the schema and seeds default data. This means:

- No separate migration step
- No manual database setup
- Fresh Docker volumes start with a clean, seeded database automatically

Initialization is deliberately *not* deferred to the first request. The snapshot task (below) and the market data poller both run without any user interaction — a container that nobody opens a browser against would otherwise have background tasks querying tables that do not exist yet. Ordering is: open database → create schema and seed if needed → start background tasks → accept requests.

Background tasks must also be supervised: if one raises, the exception is logged and the task restarts with a backoff rather than dying silently and leaving the app running with a frozen price cache or no snapshots.

### Schema

All tables include a `user_id` column defaulting to `"default"`. This is hardcoded for now (single-user) but enables future multi-user support without schema migration.

**users_profile** — User state (cash balance)
- `id` TEXT PRIMARY KEY (default: `"default"`)
- `cash_balance` REAL (default: `10000.0`)
- `created_at` TEXT (ISO timestamp)

**watchlist** — Tickers the user is watching
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `ticker` TEXT
- `added_at` TEXT (ISO timestamp)
- UNIQUE constraint on `(user_id, ticker)`

**positions** — Current holdings (one row per ticker per user)
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `ticker` TEXT
- `quantity` REAL (fractional shares supported)
- `avg_cost` REAL
- `updated_at` TEXT (ISO timestamp)
- UNIQUE constraint on `(user_id, ticker)`

*Position lifecycle:* a row exists only while the holding is open. **When a sell brings `quantity` below the epsilon `1e-6`, the position row is deleted** rather than left at zero. Because `quantity` is REAL, "sell everything" arithmetic can leave a residue like `2e-13`; the epsilon absorbs it, and the residual quantity is included in the sell so cash and the trade log stay consistent. A sell for more than the held quantity (beyond the same epsilon) is rejected — it is never silently clamped. This makes the state binary: the positions table and heatmap never show phantom 0-share holdings, and the E2E assertion "position disappears" is deterministic.

**trades** — Trade history (append-only log)
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `ticker` TEXT
- `side` TEXT (`"buy"` or `"sell"`)
- `quantity` REAL (fractional shares supported)
- `price` REAL
- `executed_at` TEXT (ISO timestamp)

**portfolio_snapshots** — Portfolio value over time (for P&L chart). Recorded every 30 seconds by a background task, and immediately after each trade execution.
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `total_value` REAL
- `recorded_at` TEXT (ISO timestamp)
- INDEX on `(user_id, recorded_at)`

*Retention:* at 30-second cadence this accrues ~2,880 rows/day and the Docker volume persists indefinitely, so the snapshot task also prunes rows older than **30 days** on each run. Reads are bounded separately at the API layer (see `/api/portfolio/history` in §8).

**chat_messages** — Conversation history with LLM
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `role` TEXT (`"user"` or `"assistant"`)
- `content` TEXT
- `actions` TEXT (JSON — the per-action results array defined in §9, including failures and their reasons; null for user messages)
- `created_at` TEXT (ISO timestamp)

### Default Seed Data

- One user profile: `id="default"`, `cash_balance=10000.0`
- Ten watchlist entries: AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA, META, JPM, V, NFLX

---

## 8. API Endpoints

### Market Data
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/stream/prices` | SSE stream of live price updates |
| GET | `/api/prices/{ticker}/history` | Recent price points for the ticker, from the cache's in-memory ring buffer |

`/api/prices/{ticker}/history` returns `{ticker, reference_price, reference_kind, points: [{t, price}]}`, oldest first, capped at the buffer size (~2,000 points). It exists so the **main detail chart** has shape the moment a ticker is selected and after every page reload, instead of being empty until the next tick. Because the buffer is in-memory, a fresh container returns few or no points — that is expected and the chart simply fills in from SSE. Returns `404` for a ticker that is not tracked.

Watchlist sparklines deliberately do *not* use this endpoint; they remain accumulated from SSE since page load, as specified in §2.

### Portfolio
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/portfolio` | Current positions, cash balance, total value, unrealized P&L |
| POST | `/api/portfolio/trade` | Execute a trade: `{ticker, quantity, side}` |
| GET | `/api/portfolio/history` | Portfolio value snapshots over time (for P&L chart) |

`/api/portfolio/history` accepts `?since=<ISO timestamp>&limit=<int>`, defaulting to the last **24 hours** with a `limit` of **500** (max 2,000). When the selected range holds more rows than `limit`, the server downsamples evenly across the range rather than truncating, so the P&L chart keeps the shape of the whole window. Without these bounds a week of uptime would push ~20,000 points into the chart on every page load.

`GET /api/portfolio` values every open position from the price cache. If a position's ticker is `unavailable`, its market value falls back to `quantity × avg_cost`, and the response flags the position `priced: false` so the UI can mark it rather than silently reporting a fabricated P&L.

#### Trade Execution Rules

These apply identically to manual trades and to LLM-initiated trades (§9) — one code path, one set of validations, evaluated in this order:

| Condition | Result |
|---|---|
| `quantity` is missing, non-numeric, ≤ 0, or not finite | `400` — invalid quantity |
| `side` is not exactly `"buy"` or `"sell"` | `400` — invalid side |
| Ticker fails format validation (1–5 A–Z characters) | `400` — invalid ticker |
| **Ticker has no usable price** — not tracked, or cached `status` is `"unavailable"`, or no tick has arrived yet | **`409` — no price available for {ticker}** |
| Buy where `quantity × price` exceeds `cash_balance` | `422` — insufficient cash |
| Sell where `quantity` exceeds held quantity (plus `1e-6` epsilon) | `422` — insufficient shares |
| Otherwise | `200` — instant fill at the cached price |

The `409` case is the one that is easy to miss and expensive to get wrong. The trade bar accepts free-text tickers and fills come from the in-memory cache, so a symbol added moments ago (before its first tick) or one the data source does not recognise will have no price. Without an explicit refusal the handler either raises mid-write or fills at `0.0` — granting free shares and corrupting both `avg_cost` and the cash balance. **A trade must never execute against a `None`, zero, or stale-unavailable price.**

A successful trade is a single atomic transaction: append to `trades`, upsert or delete the `positions` row (per the epsilon rule in §7), update `cash_balance`, and write a `portfolio_snapshots` row.

### Watchlist
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/watchlist` | Current watchlist tickers with latest prices |
| POST | `/api/watchlist` | Add a ticker: `{ticker}` |
| DELETE | `/api/watchlist/{ticker}` | Remove a ticker |

- `POST` normalizes the ticker (trim, uppercase) and validates the format (1–5 A–Z characters), returning `400` otherwise. Adding a ticker already on the watchlist is idempotent — `200`, no duplicate row. The ticker joins the tracked set immediately, so pricing begins on the next cycle.
- `GET` returns each row with its cache fields, including `status`; a ticker awaiting its first tick reports `status: "pending"` and a null price so the UI can render `—` instead of a blank cell.
- `DELETE` removes the watchlist row only. **It never affects positions, and it does not stop price tracking for a ticker you still hold** — the ticker remains in the tracked set via the open position (§6). Removing a ticker that isn't on the watchlist returns `204` (idempotent).

### Chat
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/chat` | Send a message, receive complete JSON response (message + per-action execution results) |

### System
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check (for Docker/deployment) |

---

## 9. LLM Integration

When writing code to make calls to LLMs, use the **`cerebras`** skill (invoked as `/cerebras`; its `SKILL.md` carries the internal name `cerebras-inference`) to use LiteLLM via OpenRouter to the `openrouter/openai/gpt-oss-120b` model with Cerebras as the inference provider. Structured Outputs should be used to interpret the results. Do not write the LiteLLM call from memory — the skill carries the provider-routing detail that pins inference to Cerebras.

`OPENROUTER_API_KEY` is supplied through the process environment (§5).

### How It Works

When the user sends a chat message, the backend:

1. Loads the user's current portfolio context (cash, positions with P&L, watchlist with live prices, total portfolio value)
2. Loads recent conversation history from the `chat_messages` table
3. Constructs a prompt with a system message, portfolio context, conversation history, and the user's new message
4. Calls the LLM via LiteLLM → OpenRouter, requesting structured output, using the `cerebras` skill
5. Parses the complete structured JSON response
6. Auto-executes any trades or watchlist changes specified in the response, **capturing a per-action result (applied or failed, with a reason)**
7. Stores the message and the action results in `chat_messages`
8. Returns the message plus the action results to the frontend (no token-by-token streaming — Cerebras inference is fast enough that a loading indicator is sufficient)

There is exactly one LLM call per user message. The model writes its message *before* execution happens, which means **the model's prose is not evidence that anything succeeded.** The `actions` array in the response is the authoritative record, and the UI renders it (§10).

### Structured Output Schema

The LLM is instructed to respond with JSON matching this schema:

```json
{
  "message": "Your conversational response to the user",
  "trades": [
    {"ticker": "AAPL", "side": "buy", "quantity": 10}
  ],
  "watchlist_changes": [
    {"ticker": "PYPL", "action": "add"},
    {"ticker": "NFLX", "action": "remove"}
  ]
}
```

- `message` (required): The conversational text shown to the user
- `trades` (optional): Array of trades to auto-execute
  - `ticker` — string, 1–5 A–Z characters
  - `side` — **enum, exactly `"buy"` or `"sell"`**
  - `quantity` — number, **strictly greater than 0** (fractional allowed)
- `watchlist_changes` (optional): Array of watchlist modifications
  - `ticker` — string, 1–5 A–Z characters
  - `action` — **enum, exactly `"add"` or `"remove"`**

The enums and the quantity bound must be declared in the Pydantic model backing the structured output, not merely described in the prompt. Left undeclared, the model can plausibly emit `"delete"`, `"sell_all"`, or a negative quantity, and the executor either throws on parse or silently no-ops an action the user was just told had happened.

### Auto-Execution

Trades specified by the LLM execute automatically — no confirmation dialog. This is a deliberate design choice:
- It's a simulated environment with fake money, so the stakes are zero
- It creates an impressive, fluid demo experience
- It demonstrates agentic AI capabilities — the core theme of the course

Every action runs through the same validations as a manual request — the Trade Execution Rules in §8 for trades, the watchlist rules for watchlist changes. Actions are applied in the order given, and one failure does not abort the rest.

#### Reporting Results Honestly

Because the model has already composed its message by the time execution runs, and there is no second LLM call, **the response body must carry per-action outcomes and the UI must render them.** `POST /api/chat` returns:

```json
{
  "message": "Buying 100 NVDA to lift your tech weighting.",
  "actions": [
    {
      "kind": "trade",
      "status": "failed",
      "detail": {"ticker": "NVDA", "side": "buy", "quantity": 100},
      "error": "Insufficient cash: need $18,432.00, have $10,000.00"
    }
  ]
}
```

- `kind` — `"trade"` or `"watchlist_change"`
- `status` — `"applied"` or `"failed"`
- `detail` — the action as executed, including `fill_price` and `total` for applied trades
- `error` — human-readable reason, present only when `status` is `"failed"`

The failure case this closes: with $10,000 cash the user says *"buy 100 NVDA"* (~$18k). The model replies "Done — bought 100 NVDA", the executor rejects it, and with no `actions` array the user sees a confident confirmation of a trade that never happened. The chat panel must therefore show the failed action inline beneath the message, visually distinct, with the reason — the UI contradicts the prose when the prose is wrong.

Both the message and the action results are written to `chat_messages.actions`, so the outcomes survive a page reload and are included in the conversation history sent on the *next* turn. That is how the model learns the trade failed — on the following message, without an extra round-trip.

### System Prompt Guidance

The LLM should be prompted as "FinAlly, an AI trading assistant" with instructions to:
- Analyze portfolio composition, risk concentration, and P&L
- Suggest trades with reasoning
- Execute trades when the user asks or agrees
- Manage the watchlist proactively
- Be concise and data-driven in responses
- Always respond with valid structured JSON
- **Describe requested trades as intent, not as completed fact** — "Placing an order to buy 10 AAPL" rather than "Bought 10 AAPL." Execution happens after the message is written and may fail; the app reports the real outcome
- **Check the supplied cash balance and holdings before proposing a trade**, and say so when an order would not fit rather than attempting it anyway
- **Read the action results in the conversation history** — a prior turn's failed trade did not happen, and the portfolio context reflects that

### LLM Mock Mode

When `LLM_MOCK=true`, the backend returns deterministic mock responses instead of calling OpenRouter. This enables:
- Fast, free, reproducible E2E tests
- Development without an API key
- CI/CD pipelines

Mock responses are keyed off the user's message so tests can drive specific paths, and the set must include at least one response proposing a trade the portfolio cannot afford — that is the only way to exercise the failed-action rendering described above. Mocked responses go through the real execution and result-reporting path; only the LLM call is replaced.

---

## 10. Frontend Design

### Layout

The frontend is a single-page application with a dense, terminal-inspired layout. The specific component architecture and layout system is up to the Frontend Engineer, but the UI should include these elements:

- **Watchlist panel** — grid/table of watched tickers with: ticker symbol, current price (flashing green/red on change), **Change %**, and a sparkline mini-chart (accumulated from SSE since page load). Change % is `(price − reference_price) / reference_price` straight from the SSE event — *not* the tick-to-tick delta, which at a 500ms cadence is a fraction of a percent and would be wrong by orders of magnitude if labelled as a daily move. Label the column **Change %** and disclose the baseline (tooltip or column subtitle: "since previous close" or "since session open", driven by `reference_kind`). Tickers with `status` other than `ok` render `—`.
- **Main chart area** — larger chart for the currently selected ticker, price over time. Clicking a ticker in the watchlist selects it here. On selection, fetch `GET /api/prices/{ticker}/history` to seed the series, then append live points from SSE. Without the backfill this chart would hold only since-page-load points and be empty after every reload.
- **Portfolio heatmap** — treemap visualization where each rectangle is a position, sized by portfolio weight, colored by P&L (green = profit, red = loss)
- **P&L chart** — line chart of total portfolio value over time, from `/api/portfolio/history` (backed by `portfolio_snapshots`), which is windowed and downsampled server-side (§8)
- **Positions table** — tabular view of all positions: ticker, quantity, avg cost, current price, unrealized P&L, % change. A position whose ticker is unpriced (`priced: false`) shows `—` for current price and P&L rather than a fabricated number.
- **Trade bar** — simple input area: ticker field, quantity field, buy button, sell button. Market orders, instant fill. Rejections from §8 surface as an inline error next to the bar — in particular `409 no price available`, which the free-text ticker field makes easy to hit.
- **AI chat panel** — docked/collapsible sidebar. Message input, scrolling conversation history, loading indicator while waiting for LLM response. Beneath each assistant message, render the `actions` array: applied trades and watchlist changes as confirmations, **failed ones visually distinct with their error text**. The assistant's prose is written before execution and can claim a trade that was rejected — the action list is the authoritative record and must be shown even when it contradicts the message above it.
- **Header** — portfolio total value (updating live), connection status indicator, cash balance

### Technical Notes

- Use `EventSource` for SSE connection to `/api/stream/prices`
- Canvas-based charting library preferred (Lightweight Charts or Recharts) for performance
- Price flash effect: on receiving a new price, briefly apply a CSS class with background color transition, then remove it
- All API calls go to the same origin (`/api/*`) — no CORS configuration needed
- Tailwind CSS for styling with a custom dark theme

---

## 11. Docker & Deployment

### Multi-Stage Dockerfile

```
Stage 1: Node 20 slim
  - Copy frontend/
  - npm install && npm run build (produces static export)

Stage 2: Python 3.12 slim
  - Install uv
  - Copy backend/
  - uv sync (install Python dependencies from lockfile)
  - Copy frontend build output into a static/ directory
  - Expose port 8000
  - CMD: uvicorn serving FastAPI app
```

FastAPI serves the static frontend files and all API routes on port 8000.

### Docker Volume

The SQLite database persists via a **named Docker volume**:

```bash
docker run -v finally-data:/app/db -p 8000:8000 --env-file .env finally
```

The backend writes `finally.db` to `/app/db` inside the container. That path is backed by the named volume `finally-data`, which Docker manages — **it is not the repo's `db/` directory, and no file appears there when running under Docker.** The repo's `db/` directory (§4) is where the database lands only when the backend runs directly on the host for local development.

Inspect or reset the containerized database through Docker, not the filesystem:

```bash
docker volume inspect finally-data     # where Docker keeps it
docker volume rm finally-data          # discard all data, next start re-seeds
```

To work against the repo directory instead — handy for inspecting the file with a SQLite browser — bind-mount it explicitly: `-v "$(pwd)/db:/app/db"`. Pick one form and stay with it; the two are separate databases, and mixing them is the usual cause of "my trades disappeared."

### Start/Stop Scripts

**`scripts/start_mac.sh`** (macOS/Linux):
- Builds the Docker image if not already built (or if `--build` flag passed)
- Runs the container with the `finally-data` named volume, port mapping, and `--env-file .env`
- Prints the URL to access the app
- Optionally opens the browser

All scripts and `docker-compose.yml` must use the **same** volume form as the command above, so every entry point resolves to one database.

**`scripts/stop_mac.sh`** (macOS/Linux):
- Stops and removes the running container
- Does NOT remove the volume (data persists)

**`scripts/start_windows.ps1`** / **`scripts/stop_windows.ps1`**: PowerShell equivalents for Windows.

All scripts should be idempotent — safe to run multiple times.

### Optional Cloud Deployment

The container is designed to deploy to AWS App Runner, Render, or any container platform. A Terraform configuration for App Runner may be provided in a `deploy/` directory as a stretch goal, but is not part of the core build.

---

## 12. Testing Strategy

### Unit Tests (within `frontend/` and `backend/`)

**Backend (pytest)**:
- Market data: simulator generates valid prices, GBM math is correct, Massive API response parsing works, both implementations conform to the abstract interface, unknown tickers get a deterministic seed price, `reference_price`/`reference_kind` are set correctly for both sources
- Tracked set: a ticker with an open position stays tracked after being removed from the watchlist
- SSE: unchanged tickers emit no events (the Massive-cadence duplicate-event case), a new connection receives a full snapshot
- Portfolio: trade execution logic, P&L calculations, edge cases (selling more than owned, buying with insufficient cash, selling at a loss)
- Trade rules: every row of the §8 table, especially `409` on an unpriced/unknown ticker — assert no rows are written and cash is unchanged
- Position lifecycle: selling the full quantity deletes the row; a float residue below `1e-6` is absorbed, not left behind
- History endpoint: `since`/`limit` bounds hold and downsampling preserves range endpoints
- LLM: structured output parsing handles all valid schemas, rejects out-of-enum `side`/`action` values and non-positive quantities, graceful handling of malformed responses, trade validation within chat flow
- Chat results: a chat-initiated trade that fails validation returns `status: "failed"` with a reason in `actions` and leaves the portfolio untouched
- API routes: correct status codes, response shapes, error handling
- Startup: schema exists and is seeded before background tasks run — the snapshot task never sees a missing table

**Frontend (React Testing Library or similar)**:
- Component rendering with mock data
- Price flash animation triggers correctly on price changes
- Watchlist CRUD operations
- Portfolio display calculations
- Chat message rendering and loading state

### E2E Tests (in `test/`)

**Infrastructure**: A separate `docker-compose.test.yml` in `test/` that spins up the app container plus a Playwright container. This keeps browser dependencies out of the production image.

**Environment**: Tests run with `LLM_MOCK=true` by default for speed and determinism.

**Key Scenarios**:
- Fresh start: default watchlist appears, $10k balance shown, prices are streaming
- Add and remove a ticker from the watchlist
- Buy shares: cash decreases, position appears, portfolio updates
- Sell entire position: cash increases and the position row disappears from both the table and the heatmap (deterministic per the epsilon rule in §7)
- Rejected trade: attempt a buy exceeding cash — an inline error appears, cash and positions are unchanged
- Unpriced ticker: type a symbol with no price into the trade bar — the trade is refused with a visible message, no phantom fill at $0
- Held ticker removed from watchlist: its price keeps updating and portfolio value stays live
- Detail chart backfill: select a ticker, reload the page, confirm the chart has history immediately rather than starting empty
- Portfolio visualization: heatmap renders with correct colors, P&L chart has data points
- AI chat (mocked): send a message, receive a response, trade execution appears inline
- AI chat failed action (mocked): mock a trade the portfolio cannot afford — the failure and its reason render beneath the assistant message even though the message claims success
- SSE resilience: disconnect and verify reconnection, and that the reconnect snapshot repopulates prices

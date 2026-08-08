-- FinAlly database schema (`PLAN.md` §7).
--
-- Every statement is idempotent (`IF NOT EXISTS`) so this file can be run
-- against an existing database on every startup without a migration step,
-- which is exactly what `PLAN.md` §7 asks for: "No separate migration
-- step / No manual database setup".
--
-- Every table carries a `user_id` defaulting to 'default'. That is
-- hardcoded to a single user today, but keeps the schema multi-user-ready
-- without a later migration.

-- User state (cash balance).
CREATE TABLE IF NOT EXISTS users_profile (
    id           TEXT PRIMARY KEY DEFAULT 'default',
    cash_balance REAL NOT NULL DEFAULT 10000.0,
    created_at   TEXT NOT NULL
);

-- Tickers the user is watching.
CREATE TABLE IF NOT EXISTS watchlist (
    id       TEXT PRIMARY KEY,
    user_id  TEXT NOT NULL DEFAULT 'default',
    ticker   TEXT NOT NULL,
    added_at TEXT NOT NULL,
    UNIQUE (user_id, ticker)
);

-- Current holdings, one row per ticker per user.
--
-- A row exists only while the holding is open: when a sell brings
-- `quantity` below 1e-6 the row is deleted rather than left at zero
-- (`PLAN.md` §7). The trade executor owns that rule; this file only
-- declares the shape it operates on.
CREATE TABLE IF NOT EXISTS positions (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL DEFAULT 'default',
    ticker     TEXT NOT NULL,
    quantity   REAL NOT NULL,
    avg_cost   REAL NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (user_id, ticker)
);

-- Append-only trade log.
CREATE TABLE IF NOT EXISTS trades (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL DEFAULT 'default',
    ticker      TEXT NOT NULL,
    side        TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    quantity    REAL NOT NULL,
    price       REAL NOT NULL,
    executed_at TEXT NOT NULL
);

-- Portfolio value over time, for the P&L chart.
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL DEFAULT 'default',
    total_value REAL NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_portfolio_snapshots_user_recorded
    ON portfolio_snapshots (user_id, recorded_at);

-- Conversation history with the LLM. `actions` holds the per-action
-- results array (`PLAN.md` §9), JSON-encoded; NULL for user messages.
CREATE TABLE IF NOT EXISTS chat_messages (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL DEFAULT 'default',
    role       TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content    TEXT NOT NULL,
    actions    TEXT,
    created_at TEXT NOT NULL
);

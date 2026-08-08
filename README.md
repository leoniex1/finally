# FinAlly — the Finance Ally

An AI-powered trading workstation: live streaming market data, a simulated $10,000 portfolio, and an LLM chat assistant that can analyze positions and execute trades on your behalf. Dark, data-dense, Bloomberg-terminal aesthetic.

Capstone project for an agentic AI coding course — built end to end by orchestrated coding agents that coordinate through files in `planning/`.

> **Status:** in development. The full specification lives in [`planning/PLAN.md`](planning/PLAN.md) and is the contract every agent builds against.

## Features

- **Live prices** for a 10-ticker watchlist, streamed over SSE, with green/red flash animations and sparklines
- **Detail chart** for the selected ticker, backfilled from server-side price history
- **Trading** — market orders, instant fill, fractional shares, no fees
- **Portfolio** — positions table, P&L chart over time, and a treemap heatmap sized by weight and colored by P&L
- **AI chat** — ask about your portfolio; the assistant executes trades and watchlist changes, and every action reports its real outcome (including failures) inline

## Stack

| Layer | Choice |
|---|---|
| Frontend | Next.js + TypeScript, static export, Tailwind |
| Backend | FastAPI (Python, managed with `uv`) |
| Database | SQLite, volume-mounted |
| Real-time | Server-Sent Events |
| Market data | Built-in GBM simulator, or Massive/Polygon if a key is provided |
| LLM | LiteLLM → OpenRouter (`openai/gpt-oss-120b` on Cerebras), structured outputs |

Everything runs in one container on one port — FastAPI serves both `/api/*` and the static frontend.

## Quick Start

```bash
cp .env.example .env      # add your OPENROUTER_API_KEY
./scripts/start_mac.sh    # or: scripts\start_windows.ps1
```

Then open <http://localhost:8000>. No login, no signup.

Stop with `./scripts/stop_mac.sh` (or `scripts\stop_windows.ps1`). Data persists in the `finally-data` Docker volume; `docker volume rm finally-data` resets to a fresh seeded database.

## Environment

```bash
OPENROUTER_API_KEY=     # required for chat; absent only disables chat, app still runs
MASSIVE_API_KEY=        # optional; empty = built-in market simulator
LLM_MOCK=false          # true = deterministic mock LLM responses (E2E tests)
```

## Layout

```
frontend/   Next.js static export
backend/    FastAPI app, schema + seed, market data, SSE, LLM
planning/   PLAN.md and agent reference docs — the shared contract
scripts/    start/stop wrappers for macOS/Linux and Windows
test/       Playwright E2E tests
```

## Testing

- `backend/` — pytest (market data, trade rules, portfolio math, chat actions, API routes)
- `frontend/` — component tests with React Testing Library
- `test/` — Playwright E2E against the real container, run with `LLM_MOCK=true`

## License

See [LICENSE](LICENSE).

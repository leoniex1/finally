# Massive API — Research & Reference

## How this document was researched

`massive.com` (and its subdomains, plus the legacy `polygon.io` domain) are blocked by this
session's network egress policy — every fetch attempt returned a 403 at the proxy `CONNECT`
step. This is a policy denial, not a transient failure, so it was not retried or routed around.

Instead, this document is grounded in two verifiable, unblocked sources:

1. **The official open-source Python client**, [`massive-com/client-python`](https://github.com/massive-com/client-python)
   (cloned read-only from GitHub) — its request-building code, its typed response models
   (which map every JSON field, including the terse single-letter keys the wire format uses),
   and — most valuably — its **test fixtures**, which are literal recorded JSON response bodies
   used to test the client. Endpoint paths, parameter names, and every JSON shape quoted below
   are copied from this source, not recalled from memory.
2. **Web search** result snippets that quote or summarize the hosted docs at `massive.com/docs`
   and `massive.com/knowledge-base` (pricing tiers, rate limits, ticker-count limits) — used only
   where the client repo doesn't cover the topic (account/billing behavior isn't in the SDK).

Anywhere a detail could not be confirmed from the client repo and rests only on a search-engine
summary, it's flagged inline as **(reported, unverified against primary docs)**. Before wiring a
paid plan up to real trading logic, re-verify pricing/limits directly against
`https://massive.com/pricing` and `https://massive.com/docs` once that domain is reachable.

---

## 1. What Massive Is

Massive is the rebrand of **Polygon.io**, effective **October 30, 2025**. Existing Polygon.io API
keys, accounts, and integrations continue to work unchanged. The only practical change is the
default API host:

- New default: `https://api.massive.com`
- Legacy host (still supported "for an extended period" per the client README):
  `https://api.polygon.io`

Massive/Polygon provides REST, WebSocket, and flat-file access to US-market data: stocks,
options, indices, forex, and crypto, sourced from exchanges, dark pools, FINRA TRFs, and OTC
markets. This project only needs the **stocks REST API**, specifically real-time-ish snapshots
and previous-close/EOD data — no WebSocket, no options/forex/crypto.

Source: [massive-com/client-python README](https://github.com/massive-com/client-python/blob/master/README.md)

---

## 2. Authentication

Every request carries the API key as a **bearer token in the `Authorization` header** — not a
query parameter (older Polygon.io docs/examples using `?apiKey=...` still work on many endpoints,
but the officially current convention is the header):

```
Authorization: Bearer <MASSIVE_API_KEY>
```

The official Python client reads the key from the constructor or from the environment variable
**`MASSIVE_API_KEY`** by default — this is the SDK's own convention, and it's the exact name
`PLAN.md` already specifies for this project, so no translation layer is needed:

```python
# massive/rest/__init__.py (excerpt)
BASE = "https://api.massive.com"
ENV_KEY = "MASSIVE_API_KEY"

class RESTClient(...):
    def __init__(self, api_key: Optional[str] = os.getenv(ENV_KEY), ...):
        ...
```

```python
# massive/rest/base.py (excerpt)
self.headers = {
    "Authorization": "Bearer " + self.API_KEY,
    "Accept-Encoding": "gzip",
    "User-Agent": f"Massive.com PythonClient/{version_number}",
}
```

A missing/invalid key raises `AuthError` client-side (SDK) or gets a `401`/`403` from the API
directly if you're calling REST without the SDK.

Source: [`massive/rest/__init__.py`](https://github.com/massive-com/client-python/blob/master/massive/rest/__init__.py), [`massive/rest/base.py`](https://github.com/massive-com/client-python/blob/master/massive/rest/base.py)

---

## 3. Official Python Client vs. Raw REST

```bash
pip install massive   # PyPI package name is "massive", requires Python >= 3.9
```

```python
from massive import RESTClient
client = RESTClient(api_key="...")   # or leave blank to read MASSIVE_API_KEY
```

**Important for this project: the official client is synchronous.** It's built on `urllib3`'s
blocking `PoolManager` — there is no `async`/`await` anywhere in the REST client (only the
WebSocket client uses `asyncio`, and we're not using the WebSocket API per `PLAN.md` §6). Calling
`client.get_snapshot_all(...)` directly from an `async def` FastAPI background task would block
the event loop for the duration of the HTTP round-trip.

Two ways to reconcile this with our async backend, in order of preference:

1. **Skip the SDK, call REST directly with `httpx.AsyncClient`.** The endpoints are plain JSON
   GETs — there's no meaningful SDK value-add for the handful of calls we need (snapshot,
   previous-close), and this matches the pattern the rest of the backend already uses (`httpx`
   in `MassiveSource`, per `market-data-design.md` §8). This is the approach used throughout
   the rest of this document and in `MARKET_INTERFACE.md`.
2. **Use the SDK but run it in a thread**, e.g. `await asyncio.to_thread(client.get_snapshot_all, ...)`,
   if the SDK's built-in retry/backoff (§7 below) is wanted without reimplementing it. Viable, but
   pulls in a synchronous dependency and a thread-pool hop for no functional gain over (1) given
   how small our request surface is.

This project uses **(1)** — direct `httpx` calls, matching the header/auth/param shapes the SDK
itself constructs (verified against `base.py` above), so behavior stays identical without the
blocking-call risk.

Source: [`massive/rest/base.py`](https://github.com/massive-com/client-python/blob/master/massive/rest/base.py)

---

## 4. Ticker Symbol Format

Plain stock tickers are used bare (`AAPL`, `NVDA`). Other asset classes use a type prefix — not
relevant to this project (stocks only) but useful to recognize if it ever appears in a response
or example: `O:` options, `C:` forex, `X:` crypto, `I:` indices. A malformed/unknown symbol is
never rejected client-side; the API returns per-symbol errors (§8) or an empty result, never a
guessed price.

---

## 5. Endpoint Reference

All paths below are relative to `https://api.massive.com`. Every example includes the exact
JSON shape as recorded in the client's own test fixtures — not paraphrased.

### 5.1 Multi-Ticker Snapshot — **the endpoint this project polls**

```
GET /v2/snapshot/locale/us/markets/stocks/tickers?tickers=AAPL,GOOGL,MSFT
```

Returns the latest trade, latest quote, current-minute bar, current-day bar, and **previous
day's bar** for every ticker requested, in one call — exactly the shape our shared price cache
needs (§6 of `market-data-design.md`): a live price plus a previous-close reference, batched.

Real recorded response (from the client's test suite, `test_rest/mocks/v2/snapshot/locale/us/markets/stocks/tickers/index.json`):

```json
{
  "count": 1,
  "status": "OK",
  "tickers": [
    {
      "ticker": "BCAT",
      "day": { "c": 20.506, "h": 20.64, "l": 20.506, "o": 20.64, "v": 37216, "vw": 20.616 },
      "prevDay": { "c": 20.63, "h": 21, "l": 20.5, "o": 20.79, "v": 292738, "vw": 20.6939 },
      "lastTrade": { "p": 20.506, "s": 2416, "t": 1605192894630916600, "x": 4, "i": "71675577320245", "c": [14, 41] },
      "lastQuote": { "p": 20.5, "P": 20.6, "s": 13, "S": 22, "t": 1605192959994246100 },
      "min": { "c": 20.506, "h": 20.506, "l": 20.506, "o": 20.506, "v": 5000, "vw": 20.5105, "av": 37216, "t": 1684428600000, "n": 5 },
      "todaysChange": -0.124,
      "todaysChangePerc": -0.601,
      "updated": 1605192894630916600,
      "fmv": 20.5
    }
  ]
}
```

Field notes (confirmed against the client's deserializer, `massive/rest/models/snapshot.py`):

| Field | Meaning |
|---|---|
| `day.c` / `day.o` / `day.h` / `day.l` / `day.v` / `day.vw` | Today's session bar so far (close-so-far, open, high, low, volume, VWAP) |
| `prevDay.c` | **Previous trading day's close — the value to use as `reference_price` with `reference_kind="prev_close"`** |
| `lastTrade.p` | Last trade price — **the value to use as `price`** |
| `lastTrade.t` | Last trade SIP timestamp, **nanoseconds since epoch** (divide by `1e9` for seconds) |
| `lastQuote.p` / `lastQuote.P` | Last quote **bid** / **ask** price (lowercase = bid, uppercase = ask — consistent SDK-wide) |
| `updated` | Nanosecond timestamp of the freshest of trade/quote/minute data — usable as a single "how fresh is this row" field |
| `todaysChangePerc` | Massive's own change % since previous close — we don't use this directly since our cache computes `change_pct` itself from `price`/`reference_price`, but it's a useful cross-check in manual testing |
| `fmv` | "Fair market value" — a Business-plan-only fair-value estimate for after-hours/illiquid symbols; absent without that plan, safe to ignore |

**On the free tier this endpoint is delayed/EOD, not real-time** — see §9. The shape is identical
regardless of plan; only the freshness of `lastTrade`/`lastQuote` changes.

Client method: `RESTClient.get_snapshot_all("stocks", tickers=[...])` →
`GET /v2/snapshot/locale/{locale}/markets/{market_type}/tickers`.

Source: [`massive/rest/snapshot.py`](https://github.com/massive-com/client-python/blob/master/massive/rest/snapshot.py), [`massive/rest/models/snapshot.py`](https://github.com/massive-com/client-python/blob/master/massive/rest/models/snapshot.py), [test fixture](https://github.com/massive-com/client-python/blob/master/test_rest/mocks/v2/snapshot/locale/us/markets/stocks/tickers/index.json)

### 5.2 Single-Ticker Snapshot

```
GET /v2/snapshot/locale/us/markets/stocks/tickers/{ticker}
```

Same per-ticker shape as §5.1, wrapped as `{"ticker": {...}}` instead of `{"tickers": [...]}`.
Not used by this project — we always poll the whole tracked set in one batched call (§5.1), never
one ticker at a time, to stay within the free tier's tight request budget (§9).

### 5.3 Universal / Unified Snapshot (v3) — notable for its error handling, not otherwise used

```
GET /v3/snapshot?ticker.any_of=AAPL,TSLAAPL,NCLH
```

Multi-asset-class snapshot (stocks, options, forex, crypto, indices in one response), capped at
**250 symbols per request** (confirmed both in the client's docstring and in Massive's knowledge
base). Field names here differ from §5.1 (`session` instead of `day`/`prevDay`, `last_trade`
instead of `lastTrade`, snake_case throughout) — **do not mix the two shapes**; this project uses
only the v2 endpoint (§5.1) for consistency with its EOD counterpart (§5.4), which is also v2.

The reason this endpoint is worth documenting even though unused: its test fixture shows exactly
how Massive reports **an individual bad ticker inside an otherwise-successful batch request** —
proof that "one bad symbol in the tracked set" degrades gracefully rather than failing the whole
poll, which is the behavior `MARKET_INTERFACE.md` relies on generally (even though the actual
per-ticker-absent handling in our design is against §5.1, which the next section covers):

```json
{
  "status": "OK",
  "results": [
    { "ticker": "AAPL", "type": "stocks", "session": { "close": 21.4, "...": "..." } },
    { "ticker": "TSLAAPL", "error": "NOT_FOUND", "message": "Ticker not found." }
  ]
}
```

Source: [`massive/rest/snapshot.py`](https://github.com/massive-com/client-python/blob/master/massive/rest/snapshot.py) (`list_universal_snapshots`), [test fixture](https://github.com/massive-com/client-python/blob/master/test_rest/mocks/v3/snapshot.json), [test_snapshots.py](https://github.com/massive-com/client-python/blob/master/test_rest/test_snapshots.py)

### 5.4 Previous Close (EOD reference)

```
GET /v2/aggs/ticker/{ticker}/prev
```

Real recorded response (`test_rest/mocks/v2/aggs/ticker/AAPL/prev.json`):

```json
{
  "ticker": "AAPL",
  "queryCount": 1,
  "resultsCount": 1,
  "adjusted": true,
  "results": [
    { "T": "AAPL", "o": 162.25, "c": 156.8, "h": 162.34, "l": 156.72, "v": 95595226.0, "vw": 158.6074, "t": 1651003200000, "n": 899965 }
  ],
  "status": "OK"
}
```

Used as a fallback to seed `reference_price`/`reference_kind="prev_close"` for a ticker whose
first snapshot poll didn't carry a usable `prevDay` (e.g. a ticker with no trading history the
prior session). Not needed on every poll — §5.1 already returns `prevDay` inline for the common
case, so this is a rare supplementary call, not part of the steady-state polling loop.

### 5.5 Daily Open/Close

```
GET /v1/open-close/{ticker}/{date}
```

Response: `{"status", "from", "symbol", "open", "high", "low", "close", "volume", "afterHours", "preMarket", "otc"}`
for one named calendar date. Not used by this project (§5.1/§5.4 cover our needs), documented for
completeness since `PLAN.md` mentions "end of day prices" explicitly.

### 5.6 Aggregates / Bars (historical range)

```
GET /v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from}/{to}
```

E.g. `GET /v2/aggs/ticker/AAPL/range/1/minute/2023-01-01/2023-06-13?limit=50000`. Returns an
array of OHLCV bars (`o,h,l,c,v,vw,t,n`). Not used by this project — our chart backfill comes from
the in-process price-cache ring buffer (`market-data-design.md` §4/§13), not from Massive
historical bars; this is documented for completeness/future use (e.g. a "load 1 year of daily
history" feature would use this).

### 5.7 Grouped Daily (whole-market EOD)

```
GET /v2/aggs/grouped/locale/us/market/stocks/{date}
```

One OHLCV row per ticker for an entire market on one date. Not used — we only ever need our
tracked set, never the whole market.

### 5.8 Last Trade / Last Quote (single ticker, single value)

```
GET /v2/last/trade/{ticker}
GET /v2/last/nbbo/{ticker}
```

Lower-level single-value equivalents of pieces of the snapshot (§5.1). Not used — the snapshot
endpoint already returns both in one batched call per ticker.

---

## 6. Full Endpoint Summary

| Purpose | Method & Path | Used by this project |
|---|---|---|
| Batched real-time-ish price + prev-close for N tickers | `GET /v2/snapshot/locale/us/markets/stocks/tickers?tickers=...` | **Yes — primary polling endpoint** |
| Single-ticker snapshot | `GET /v2/snapshot/locale/us/markets/stocks/tickers/{ticker}` | No |
| Multi-asset unified snapshot (250 cap) | `GET /v3/snapshot?ticker.any_of=...` | No |
| Previous close (single ticker) | `GET /v2/aggs/ticker/{ticker}/prev` | Fallback only |
| Daily open/close (named date) | `GET /v1/open-close/{ticker}/{date}` | No |
| Historical bars/aggregates | `GET /v2/aggs/ticker/{ticker}/range/{mult}/{span}/{from}/{to}` | No |
| Whole-market grouped daily | `GET /v2/aggs/grouped/locale/us/market/stocks/{date}` | No |
| Last trade (single ticker) | `GET /v2/last/trade/{ticker}` | No |
| Last quote/NBBO (single ticker) | `GET /v2/last/nbbo/{ticker}` | No |

---

## 7. Error Handling & Retries

From the SDK's own retry policy (`massive/rest/base.py`), which is a faithful mirror of how the
API signals transient vs. permanent failure:

```python
retry_strategy = Retry(
    total=3,
    status_forcelist=[413, 429, 499, 500, 502, 503, 504],  # retryable
    backoff_factor=0.1,   # 0.0s, 0.2s, 0.4s, 0.8s, 1.6s, ...
)
```

- **`429`** — rate limit exceeded (§9). Retryable with backoff.
- **`403`** — plan does not entitle you to the requested data (e.g. real-time snapshots on the
  free tier — see §9's caveat). **Not retryable**; retrying doesn't fix an entitlement problem.
- **`404`** — path-level not-found (bad ticker in a *single-ticker* endpoint like §5.4/§5.5).
- **Any non-`200`** — the SDK raises `BadResponse(resp.data.decode("utf-8"))`, i.e. surfaces the
  raw response body as the exception message; our own client should log the body too, since
  Massive's error bodies are informative (`{"status": "NOT_AUTHORIZED", "error": "..."}` shapes
  reported in search results — not directly confirmed against a live 403, so treat the exact JSON
  key names as indicative, not guaranteed).
- **Per-ticker absence inside a successful batch response is not an HTTP error at all** — see
  §5.1/§5.3: a `200 OK` response simply omits the ticker (v2) or includes an `error`/`message`
  pair for that entry (v3). This is the case `MARKET_INTERFACE.md` design must handle explicitly
  — a missing ticker in an otherwise-`200` response is exactly the `"unavailable"` case from
  `PLAN.md` §6, not a request failure to retry.

Source: [`massive/rest/base.py`](https://github.com/massive-com/client-python/blob/master/massive/rest/base.py), [`massive/exceptions.py`](https://github.com/massive-com/client-python/blob/master/massive/exceptions.py)

---

## 8. Rate Limits & Plan Tiers

**(reported, unverified against primary docs — from search-engine summaries of `massive.com/pricing`
and `massive.com/knowledge-base`, not fetched directly due to the egress block in this session)**

| Tier | Price | Calls | History | Data freshness |
|---|---|---|---|---|
| Basic (free) | $0/mo | **5 calls/min** | 2 years | **End-of-day only** |
| Starter | $29/mo | Unlimited | 5 years | 15-minute delayed |
| Developer | $79/mo | Unlimited | 10 years | 15-minute delayed + trades data |
| Advanced | $199/mo | Unlimited | 20+ years | **Real-time** trades/quotes/financials |

**This is an important correction to a plan assumption.** `PLAN.md` §6 says "Free tier (5
calls/min): poll every 15 seconds," implying the free tier is usable for live-ish price
streaming at a slow cadence. The research above suggests the free tier's data is **end-of-day
only**, not merely rate-limited — i.e. `lastTrade`/`lastQuote` on the free tier may reflect the
prior session's close all day, regardless of poll frequency, and true intraday movement may
require at minimum the Starter tier (delayed) or Advanced tier (real-time).

This doesn't change anything about the *code* in `MARKET_INTERFACE.md`/`market-data-design.md` —
the polling loop, the snapshot parsing, and the `unavailable`-on-missing-ticker handling are
identical regardless of which tier's data is flowing through them. It changes only the
**operational expectation**: a student running this project with a free-tier key should expect
`MASSIVE_API_KEY`-driven prices to look "stale/flat" intraday and only move day-to-day, which is
still strictly better than nothing but is not "live ticking" in the way the simulator is. Anyone
who wants the visually-live experience the app is designed to demo either uses the simulator
(no key set) or a paid tier. **Recommendation:** call this out explicitly in the project's README
or `.env.example` comment next to `MASSIVE_API_KEY` so it isn't a surprise, and keep
`MASSIVE_POLL_INTERVAL_SECONDS` configurable (already the design in `market-data-design.md` §10)
so a paid-tier user can safely poll faster than 15s.

Sources (search-summarized, not directly fetched): "[What is the request limit for Massive's RESTful APIs?](https://massive.com/knowledge-base/article/what-is-the-request-limit-for-massives-restful-apis)", "[Pricing | Massive](https://massive.com/pricing)"

---

## 9. Recommended Usage For This Project

1. **Poll `GET /v2/snapshot/locale/us/markets/stocks/tickers?tickers=<comma-joined tracked set>`
   on a timer** (`MASSIVE_POLL_INTERVAL_SECONDS`, default matching the tier in use — 15s is the
   safe default for the free tier's 5-calls/min budget: one call per poll, `60 / 5 = 12s` floor,
   15s gives margin).
2. **For each ticker in the tracked set, look for it in the response's `tickers` array.** Present
   → `cache.update(ticker, price=lastTrade.p, reference_price=prevDay.c, reference_kind="prev_close", now=...)`.
   Absent → `cache.mark_unavailable(ticker)` — do **not** treat a whole-request `200` with a
   partial ticker list as an error; do **not** retry it faster, it will look the same next poll
   until Massive actually has data for that symbol.
3. **Only retry on transport/HTTP failure** (network error, non-`200`, malformed JSON) — and on
   retry, change nothing about cache state (§7's "leave prior cache state intact" rule, already
   encoded in `market-data-design.md` §8's `MassiveSource._poll`).
4. **Do not call the SDK synchronously from an `async def`.** Use `httpx.AsyncClient` directly,
   matching the header shape from §2, or wrap the SDK call in `asyncio.to_thread`.

### Minimal working example (raw async REST, no SDK dependency)

```python
import httpx

MASSIVE_BASE_URL = "https://api.massive.com"

async def fetch_snapshot(tickers: list[str], api_key: str) -> dict:
    async with httpx.AsyncClient(
        base_url=MASSIVE_BASE_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=8.0,
    ) as client:
        response = await client.get(
            "/v2/snapshot/locale/us/markets/stocks/tickers",
            params={"tickers": ",".join(tickers)},
        )
        response.raise_for_status()
        return response.json()

# payload["tickers"] -> list of per-ticker dicts shaped exactly as in §5.1
```

### Equivalent using the official SDK (sync — for scripts/tests, not the FastAPI event loop)

```python
from massive import RESTClient

client = RESTClient()  # reads MASSIVE_API_KEY from the environment

snapshots = client.get_snapshot_all("stocks", tickers=["AAPL", "GOOGL", "MSFT"])
for s in snapshots:
    print(s.ticker, s.last_trade.price if s.last_trade else None, s.prev_day.close if s.prev_day else None)
```

---

## 10. Sources

- [massive-com/client-python](https://github.com/massive-com/client-python) — official Python SDK source, request builders, response models, and recorded test-fixture JSON (primary source for all endpoint paths, parameters, and response shapes in this document)
- [Overview | Stocks REST API - Massive](https://massive.com/docs/rest/stocks/overview) (search-summarized only — domain blocked for direct fetch this session)
- [REST API Quickstart | Massive](https://massive.com/docs/rest/quickstart) (search-summarized only)
- [Unified Snapshot | Stocks REST API - Massive](https://massive.com/docs/rest/stocks/snapshots/unified-snapshot) (search-summarized only)
- [What is the request limit for Massive's RESTful APIs?](https://massive.com/knowledge-base/article/what-is-the-request-limit-for-massives-restful-apis) (search-summarized only)
- [What is the max number of tickers I can pass through Massive's Snapshot?](https://massive.com/knowledge-base/article/what-is-the-max-number-of-tickers-i-can-pass-through-massives-snapshot) (search-summarized only)
- [Pricing | Massive](https://massive.com/pricing) (search-summarized only)

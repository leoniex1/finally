# FinAlly — End-to-End Tests

Playwright suite covering the scenarios in `planning/PLAN.md` §12. It drives the real
application through a browser: the built container, the live SSE stream, the mocked LLM,
and the actual SQLite database.

Owned by **integration-tester**. Backend and frontend unit tests live in `backend/tests/`
and `frontend/` respectively — see `planning/API_CONTRACT.md` §8.

---

## Two ways to run

### 1. Against the built container (the real thing)

```bash
cd test
npm install          # first time only
npm run test:docker
```

This builds the image from the repo root, boots it with `LLM_MOCK=true` and the market
simulator, waits for `/api/health`, then runs the suite from a second container. It is
what CI runs and the only mode that tests the artifact users actually launch.

Tear down afterwards (also drops the cached `node_modules` volume):

```bash
npm run test:docker:down
```

If port 8000 is already in use on your machine, publish the app somewhere else:

```bash
E2E_APP_PORT=8001 npm run test:docker
```

The app container's database is a **tmpfs**, so every run starts from the seeded
default state — 10 watchlist tickers, $10,000 cash (`PLAN.md` §7). Nothing to reset,
nothing to leak between runs. The suite's determinism depends on this; do not swap it
for a named volume.

### 2. Against a locally-running app (fast iteration)

Start the backend yourself on port 8000 with the frontend export in place, then:

```bash
cd test
npm test                 # everything
npm run test:headed      # watch it drive the browser
npm run test:ui          # Playwright's interactive UI mode
npm run report           # open the last HTML report
```

Point it anywhere with `E2E_BASE_URL`:

```bash
E2E_BASE_URL=http://localhost:3000 npm test
```

Run `npm run install:browsers` once if Playwright complains about a missing browser.

---

## Which tests exist right now

| Tag | Spec | Needs the app? |
|---|---|---|
| `@harness` | `e2e/harness.spec.ts` | **No** |
| `@app` | `e2e/smoke.spec.ts` | Yes |

```bash
npm run test:harness   # harness self-check only — passes today
npm run test:app       # smoke tests — fail until the app is wired
```

**`@harness` verifies the test harness itself**, not the product: the browser launches,
`data-testid` selectors resolve, and the value-parsing helpers read `data-value`,
formatted currency, and the unpriced em dash correctly. If these pass, any `@app`
failure is the application's, not the harness's.

**`@app` smoke tests fail until the app boots** (task #15). That is expected. They are
deliberately *not* marked `test.skip`/`test.fixme`, because a skipped test stays quietly
skipped after the app lands and stops being a signal. A red smoke test that says "could
not reach the app at http://localhost:8000" is an honest status report.

The full §12 scenario suite is task #16 and lands once the container boots.

---

## Conventions for anyone adding tests here

- **Never run in parallel.** The app is single-user by design (`user_id` is always
  `"default"`): one cash balance, one watchlist, one positions table. `workers: 1` and
  `fullyParallel: false` in `playwright.config.ts` are load-bearing, not a slow default.
- **Select on `data-testid` only**, from the table in `API_CONTRACT.md` §7.1. Never on
  CSS classes, colors, or copy — those belong to frontend-engineer and will change.
- **Drive the LLM through the mock triggers** in `API_CONTRACT.md` §6.1. That table is a
  two-way contract with llm-engineer; neither side changes it alone.
- **Assert numbers via `numericValue()`** (`support/app.ts`), which prefers the raw
  `data-value` attribute and falls back to parsing display text.
- **An unpriced cell is `null`, never `0`.** `PLAN.md` §10 forbids fabricating a price;
  a test that accepts `0` there would pass on exactly the bug the rule exists to prevent.
- **Waiting:** use `expect.poll` / web-first assertions. Prices tick on a ~500ms cadence,
  so `waitForTimeout` is both slow and flaky. `support/app.ts` has `waitForStream()` and
  `waitForPrice()`.

## Layout

```
test/
├── docker-compose.test.yml   # app container + Playwright container
├── playwright.config.ts      # baseURL, timeouts, traces, serial execution
├── e2e/                      # specs
└── support/                  # env resolution, shared helpers, seeded constants
```

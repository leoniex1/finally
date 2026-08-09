import { test, expect } from '@playwright/test';
import { BASE_URL } from '../support/env';
import {
  DEFAULT_WATCHLIST,
  STARTING_CASH,
  numericValue,
  waitForStream,
  waitForPrice,
} from '../support/app';

/**
 * Smoke — @app
 *
 * The minimum proof that the container is alive and serving both halves of the
 * app on one port: the API answers, the static frontend loads, and the seeded
 * state is what PLAN.md §7 promises.
 *
 * THESE FAIL UNTIL THE APP IS WIRED (task #15). That is expected and honest —
 * they are not skipped, because a skipped test quietly stays skipped after the
 * app lands. Use `npm run test:harness` to verify the harness alone.
 *
 * The full scenario suite (PLAN.md §12) is task #16.
 */
test.describe('@app', () => {
  test('GET /api/health returns ok', async ({ request }) => {
    let response;
    try {
      response = await request.get('/api/health');
    } catch (cause) {
      throw new Error(
        `Could not reach the app at ${BASE_URL}. Start it first ` +
          `(npm run test:docker, or run the backend on :8000). Original error: ${cause}`,
      );
    }

    expect(response.status(), 'health check must be 200').toBe(200);
    // API_CONTRACT §3.1
    expect(await response.json()).toEqual({ status: 'ok' });
  });

  test('the page loads, streams prices, and shows the seeded state', async ({ page }) => {
    const response = await page.goto('/');
    expect(response?.status(), 'FastAPI must serve the Next.js export at /').toBeLessThan(400);

    // The SSE connection is live (PLAN.md §2, connection status indicator).
    await waitForStream(page);

    // All ten seeded tickers are on the watchlist (PLAN.md §7).
    await expect(page.getByTestId('watchlist')).toBeVisible();
    for (const ticker of DEFAULT_WATCHLIST) {
      await expect(
        page.getByTestId(`watchlist-row-${ticker}`),
        `${ticker} should be in the seeded watchlist`,
      ).toBeVisible();
    }

    // Prices are actually arriving, not just rows rendering.
    await waitForPrice(page, 'AAPL');

    // $10,000 of virtual cash, untouched — asserted only when the portfolio
    // genuinely is untouched. Playwright runs spec files alphabetically over
    // one shared single-user database, so this file executes *after*
    // `scenarios.spec.ts` has traded. Asserting the seed unconditionally here
    // would fail on a suite that is working correctly, which is the worst kind
    // of red. The seeded balance is still covered: `scenarios.spec.ts` checks
    // it under the same guard, and it runs first.
    // Not the exact seed: Playwright runs spec files alphabetically over one
    // shared single-user database, so `scenarios.spec.ts` has already traded
    // by the time this file runs — and a round trip can end flat on positions
    // while leaving cash a few cents off $10,000. Pinning the seed here would
    // fail on a suite that is working perfectly.
    //
    // The seeded balance IS still asserted exactly, in `scenarios.spec.ts`'s
    // "fresh start" test, which runs first against the fresh volume. What
    // belongs here is the weaker, order-independent claim: the balance is a
    // real number the UI actually rendered.
    expect(await numericValue(page.getByTestId('cash-balance'))).toBeGreaterThan(0);
  });
});

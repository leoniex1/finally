import { test, expect } from '@playwright/test';
import { BASE_URL } from '../support/env';
import { numericValue, DEFAULT_WATCHLIST, STARTING_CASH } from '../support/app';

/**
 * Harness self-check — @harness
 *
 * These tests need a browser but NOT the application, so the harness is
 * verifiable before a single line of the app exists. If these pass, the
 * Playwright install, the TypeScript wiring, the testid selectors and the
 * value-parsing helpers are all sound, and any failure in the @app specs is
 * the app's, not the harness's.
 *
 * Run with:  npm run test:harness
 */
test.describe('@harness', () => {
  test('config resolves a usable base URL', async ({ baseURL }) => {
    expect(baseURL, 'playwright.config.ts must set baseURL from support/env.ts').toBe(BASE_URL);
    expect(() => new URL(BASE_URL as string)).not.toThrow();
    // A single origin serves both the API and the UI (PLAN.md §3). The port
    // itself is deliberately not pinned: E2E_BASE_URL exists so the suite can
    // run against a container published on any port, and hardcoding 8000 here
    // made the harness fail whenever that override was used for its intended
    // purpose. That one origin serves both halves is proven by the app specs,
    // which hit `/api/*` and the static UI through this same base URL.
    expect(new URL(BASE_URL as string).port, 'base URL must carry an explicit port').not.toBe('');
  });

  test('seeded constants match the plan', async () => {
    // PLAN.md §7 "Default Seed Data" — every scenario starts from exactly this.
    expect(DEFAULT_WATCHLIST).toHaveLength(10);
    expect(STARTING_CASH).toBe(10_000);
  });

  test('browser launches and testid selectors resolve', async ({ page }) => {
    await page.setContent(`
      <div data-testid="connection-status" data-status="connected"></div>
      <table data-testid="positions-table">
        <tr data-testid="position-row-AAPL"><td>AAPL</td></tr>
      </table>
    `);

    await expect(page.getByTestId('connection-status')).toHaveAttribute('data-status', 'connected');
    await expect(page.getByTestId('positions-table')).toBeVisible();
    await expect(page.getByTestId('position-row-AAPL')).toContainText('AAPL');
    await expect(page.getByTestId('position-row-TSLA')).toHaveCount(0);
  });

  test('numeric helper reads data-value, formatted text, and the unpriced dash', async ({
    page,
  }) => {
    await page.setContent(`
      <span data-testid="raw" data-value="8234.5">$8,234.50</span>
      <span data-testid="formatted">$8,234.50</span>
      <span data-testid="negative">-$117.80</span>
      <span data-testid="unpriced">—</span>
    `);

    // data-value wins when present.
    expect(await numericValue(page.getByTestId('raw'))).toBe(8234.5);
    // Falls back to parsing the display string, so the suite survives either shape.
    expect(await numericValue(page.getByTestId('formatted'))).toBe(8234.5);
    expect(await numericValue(page.getByTestId('negative'))).toBe(-117.8);
    // An unpriced cell is null, never 0 — PLAN.md §10 forbids fabricating a number.
    expect(await numericValue(page.getByTestId('unpriced'))).toBeNull();
  });
});

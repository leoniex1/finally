import { test, expect } from '@playwright/test';
import {
  DEFAULT_WATCHLIST,
  STARTING_CASH,
  numericValue,
  requireNumericValue,
  waitForPrice,
  waitForStream,
} from '../support/app';

/**
 * The PLAN.md §12 "Key Scenarios" suite — @app
 *
 * These run serially against one shared database (the app is single-user by
 * design), so each scenario derives its expectations from the state it finds
 * rather than assuming an absolute balance. Only the first scenario asserts
 * the seeded $10,000, and it is ordered first for that reason.
 */
test.describe.configure({ mode: 'serial' });

/**
 * The chat transcript accumulates across scenarios and survives reloads, so a
 * fixed `chat-message-1` index points at whichever exchange happened to be
 * second — an earlier scenario's, once more than one has run. These anchor to
 * the most recent assistant turn instead.
 */
const lastAssistantMessage = (page: import('@playwright/test').Page) =>
  page.locator('[data-testid^="chat-message-"][data-role="assistant"]').last();

const lastAction = (page: import('@playwright/test').Page) =>
  page.locator('[data-testid^="chat-action-"]:not([data-testid*="-error-"])').last();

const lastActionError = (page: import('@playwright/test').Page) =>
  page.locator('[data-testid^="chat-action-error-"]').last();

test.describe('@app', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await waitForStream(page);
  });

  test('fresh start: default watchlist, seeded cash, prices streaming', async ({ page }) => {
    for (const ticker of DEFAULT_WATCHLIST) {
      await expect(page.getByTestId(`watchlist-row-${ticker}`)).toBeVisible();
    }

    // Seeded balance. This is the one scenario entitled to assume it, so it
    // tolerates a prior run having traded by checking the API's own seed only
    // when the portfolio is untouched.
    const positions = await page.locator('[data-testid^="position-row-"]').count();
    if (positions === 0) {
      expect(await requireNumericValue(page.getByTestId('cash-balance'))).toBeCloseTo(
        STARTING_CASH,
        2,
      );
    }

    await waitForPrice(page, 'AAPL');
  });

  test('add and remove a watchlist ticker', async ({ page }) => {
    await page.getByTestId('watchlist-add-input').fill('PYPL');
    await page.getByTestId('watchlist-add-submit').click();
    await expect(page.getByTestId('watchlist-row-PYPL')).toBeVisible();

    await page.getByTestId('watchlist-row-PYPL').hover();
    await page.getByTestId('watchlist-remove-PYPL').click();
    await expect(page.getByTestId('watchlist-row-PYPL')).toBeHidden();
  });

  test('buy shares: cash decreases, position appears', async ({ page }) => {
    const cashBefore = await requireNumericValue(page.getByTestId('cash-balance'));
    const price = await waitForPrice(page, 'AAPL');

    await page.getByTestId('trade-ticker').fill('AAPL');
    await page.getByTestId('trade-quantity').fill('3');
    await page.getByTestId('trade-buy').click();

    await expect(page.getByTestId('position-row-AAPL')).toBeVisible();
    expect(await requireNumericValue(page.getByTestId('position-qty-AAPL'))).toBeCloseTo(3, 6);

    // Prices move every ~500ms, so the fill is near the observed price rather
    // than exactly it — assert the cash moved by roughly 3 shares' worth.
    const cashAfter = await requireNumericValue(page.getByTestId('cash-balance'));
    expect(cashBefore - cashAfter).toBeGreaterThan(price * 3 * 0.9);
    expect(cashBefore - cashAfter).toBeLessThan(price * 3 * 1.1);

    // The position must also appear in the heatmap.
    await expect(page.getByTestId('heatmap-tile-AAPL')).toBeVisible();
  });

  test('rejected trade: buy exceeding cash leaves everything unchanged', async ({ page }) => {
    const cashBefore = await requireNumericValue(page.getByTestId('cash-balance'));
    const qtyBefore = await numericValue(page.getByTestId('position-qty-AAPL'));

    await page.getByTestId('trade-ticker').fill('AAPL');
    await page.getByTestId('trade-quantity').fill('100000');
    await page.getByTestId('trade-buy').click();

    await expect(page.getByTestId('trade-error')).toBeVisible();
    await expect(page.getByTestId('trade-error')).toContainText(/insufficient cash/i);

    expect(await requireNumericValue(page.getByTestId('cash-balance'))).toBeCloseTo(cashBefore, 2);
    expect(await numericValue(page.getByTestId('position-qty-AAPL'))).toBe(qtyBefore);
  });

  test('unpriced ticker: refused with a visible message, no phantom fill', async ({ page }) => {
    const cashBefore = await requireNumericValue(page.getByTestId('cash-balance'));

    await page.getByTestId('trade-ticker').fill('ZQXW');
    await page.getByTestId('trade-quantity').fill('1');
    await page.getByTestId('trade-buy').click();

    await expect(page.getByTestId('trade-error')).toContainText(/no price available/i);
    // The failure this guards: a fill at $0.00 granting free shares.
    await expect(page.getByTestId('position-row-ZQXW')).toHaveCount(0);
    expect(await requireNumericValue(page.getByTestId('cash-balance'))).toBeCloseTo(cashBefore, 2);
  });

  test('held ticker removed from the watchlist keeps repricing', async ({ page }) => {
    await expect(page.getByTestId('position-row-AAPL')).toBeVisible();

    await page.getByTestId('watchlist-row-AAPL').hover();
    await page.getByTestId('watchlist-remove-AAPL').click();
    await expect(page.getByTestId('watchlist-row-AAPL')).toBeHidden();

    // The position survives, and — the point of the scenario — its price keeps
    // arriving, because the tracked set is watchlist ∪ open positions.
    await expect(page.getByTestId('position-row-AAPL')).toBeVisible();
    const first = await requireNumericValue(page.getByTestId('position-price-AAPL'));

    await expect
      .poll(async () => await numericValue(page.getByTestId('position-price-AAPL')), {
        timeout: 20_000,
        message: 'price stopped updating for a held ticker after it left the watchlist',
      })
      .not.toBe(first);

    // Restore it for the scenarios that follow.
    await page.getByTestId('watchlist-add-input').fill('AAPL');
    await page.getByTestId('watchlist-add-submit').click();
    await expect(page.getByTestId('watchlist-row-AAPL')).toBeVisible();
  });

  test('detail chart has history immediately after a reload', async ({ page }) => {
    await page.getByTestId('watchlist-row-GOOGL').click();
    await expect(page.getByTestId('detail-chart').locator('[data-ticker="GOOGL"]')).toBeVisible();

    // Let the server accumulate a few points, then reload.
    await expect
      .poll(
        async () =>
          Number(
            await page
              .getByTestId('detail-chart')
              .locator('[data-point-count]')
              .getAttribute('data-point-count'),
          ),
        { timeout: 20_000 },
      )
      .toBeGreaterThan(2);

    await page.reload();
    await waitForStream(page);

    // The selection survived, and the series is seeded from the server rather
    // than starting empty — without the backfill this count would be 0 or 1.
    const chart = page.getByTestId('detail-chart').locator('[data-ticker]');
    await expect(chart).toHaveAttribute('data-ticker', 'GOOGL');
    expect(Number(await chart.getAttribute('data-point-count'))).toBeGreaterThan(2);
  });

  test('portfolio visualization: heatmap tile signed, P&L chart has points', async ({ page }) => {
    const tile = page.getByTestId('heatmap-tile-AAPL');
    await expect(tile).toBeVisible();
    // Semantics, not hex values — the palette belongs to the frontend.
    await expect(tile).toHaveAttribute('data-pnl-sign', /up|down|flat/);
    expect(Number(await tile.getAttribute('data-weight'))).toBeGreaterThan(0);

    await expect
      .poll(
        async () =>
          Number(
            await page
              .getByTestId('pnl-chart')
              .locator('[data-point-count]')
              .getAttribute('data-point-count'),
          ),
        { timeout: 20_000, message: 'the P&L chart never received a snapshot' },
      )
      .toBeGreaterThan(0);
  });

  test('AI chat (mocked): an executed action renders inline', async ({ page }) => {
    await page.getByTestId('chat-input').fill('add PYPL');
    await page.getByTestId('chat-send').click();

    const action = lastAction(page);
    await expect(action).toBeVisible();
    await expect(action).toHaveAttribute('data-status', 'applied');
    await expect(page.getByTestId('watchlist-row-PYPL')).toBeVisible();

    await page.getByTestId('watchlist-row-PYPL').hover();
    await page.getByTestId('watchlist-remove-PYPL').click();
  });

  test('AI chat (mocked): a failed action contradicts the prose', async ({ page }) => {
    const cashBefore = await requireNumericValue(page.getByTestId('cash-balance'));

    await page.getByTestId('chat-input').fill('buy 100 NVDA');
    await page.getByTestId('chat-send').click();

    // The assistant's message claims the buy is happening…
    await expect(lastAssistantMessage(page)).toContainText(/NVDA/i);

    // …and the action list says otherwise, with the reason. Asserting only the
    // status would pass against a UI that silently swallowed the error text.
    await expect(lastAction(page)).toHaveAttribute('data-status', 'failed');
    await expect(lastActionError(page)).toContainText(/insufficient cash/i);

    expect(await requireNumericValue(page.getByTestId('cash-balance'))).toBeCloseTo(cashBefore, 2);
  });

  test('chat transcript and its outcomes survive a reload', async ({ page }) => {
    await page.reload();
    await waitForStream(page);

    // The stored `actions` are what tell the model on the next turn that the
    // trade failed, so losing them on reload would be a real regression.
    await expect(lastActionError(page)).toContainText(/insufficient cash/i);
  });

  test('sell the entire position: row disappears from table and heatmap', async ({ page }) => {
    await expect(page.getByTestId('position-row-AAPL')).toBeVisible();
    const held = await requireNumericValue(page.getByTestId('position-qty-AAPL'));
    const cashBefore = await requireNumericValue(page.getByTestId('cash-balance'));

    await page.getByTestId('trade-ticker').fill('AAPL');
    await page.getByTestId('trade-quantity').fill(String(held));
    await page.getByTestId('trade-sell').click();

    // Deterministic per the epsilon rule (PLAN.md §7): the row is deleted, not
    // left holding a float residue.
    await expect(page.getByTestId('position-row-AAPL')).toHaveCount(0);
    await expect(page.getByTestId('heatmap-tile-AAPL')).toHaveCount(0);
    expect(await requireNumericValue(page.getByTestId('cash-balance'))).toBeGreaterThan(cashBefore);
  });

  test('SSE resilience: the stream reconnects and repopulates', async ({ page }) => {
    await waitForPrice(page, 'MSFT');

    // `context.setOffline(true)` does NOT work here: it stops *new* requests
    // but leaves an already-established SSE connection streaming, so the dot
    // stays green and the test passes without ever testing anything. Failing
    // the stream route and reloading actually severs it.
    await page.route('**/api/stream/prices', (route) => route.abort());
    await page.reload();

    await expect(page.getByTestId('connection-status')).not.toHaveAttribute(
      'data-status',
      'connected',
      { timeout: 20_000 },
    );

    await page.unroute('**/api/stream/prices');

    // EventSource retries on its own, and every reconnect begins with a full
    // snapshot — which is why there is no client-side catch-up logic and why
    // prices must return without any interaction.
    await waitForStream(page, 30_000);
    await waitForPrice(page, 'MSFT');
  });
});

import { expect, type Locator, type Page } from '@playwright/test';

/**
 * Shared helpers and the seeded facts every scenario starts from.
 *
 * The compose file gives each run a throwaway database, so these constants are
 * the *guaranteed* starting state of every test (PLAN.md §7, "Default Seed Data").
 */

export const STARTING_CASH = 10_000.0;

export const DEFAULT_WATCHLIST = [
  'AAPL',
  'GOOGL',
  'MSFT',
  'AMZN',
  'TSLA',
  'NVDA',
  'META',
  'JPM',
  'V',
  'NFLX',
] as const;

/**
 * Read a number out of a UI cell.
 *
 * Prefers the raw `data-value` attribute (API_CONTRACT §7.1) and falls back to
 * parsing the formatted text, so a cell rendered as "$8,234.50" still yields
 * 8234.5. Returns null for an unpriced cell — one that renders the em dash and
 * carries no `data-value` — which is how "priced: false" is asserted rather
 * than being confused with zero.
 */
export async function numericValue(cell: Locator): Promise<number | null> {
  const raw = await cell.getAttribute('data-value');
  if (raw !== null && raw.trim() !== '') {
    const parsed = Number(raw);
    return Number.isFinite(parsed) ? parsed : null;
  }

  const text = (await cell.textContent())?.trim() ?? '';
  if (text === '' || text === '—' || text === '-') return null;

  const cleaned = text.replace(/[^0-9.eE+-]/g, '');
  const parsed = Number(cleaned);
  if (!Number.isFinite(parsed)) return null;
  // A parenthesised or minus-prefixed figure is negative.
  return /^\(|^-/.test(text) ? -Math.abs(parsed) : parsed;
}

/** Same as numericValue but fails the test instead of returning null. */
export async function requireNumericValue(cell: Locator): Promise<number> {
  const value = await numericValue(cell);
  expect(value, `expected a numeric value in ${cell}`).not.toBeNull();
  return value as number;
}

/**
 * Wait until the SSE stream has populated a ticker's price cell.
 *
 * PLAN.md §6: a fresh client receives a full snapshot on connect, so this
 * normally resolves on the first frame — but a cold container may still be
 * waiting on its first simulator cycle.
 */
export async function waitForPrice(page: Page, ticker: string, timeout = 20_000): Promise<number> {
  const cell = page.getByTestId(`watchlist-price-${ticker}`);
  await expect(cell).toBeVisible({ timeout });
  await expect
    .poll(async () => await numericValue(cell), {
      timeout,
      message: `no price ever arrived for ${ticker}`,
    })
    .not.toBeNull();
  return (await numericValue(cell)) as number;
}

/** Wait for the connection dot to report a live stream. */
export async function waitForStream(page: Page, timeout = 20_000): Promise<void> {
  await expect(page.getByTestId('connection-status')).toHaveAttribute('data-status', 'connected', {
    timeout,
  });
}

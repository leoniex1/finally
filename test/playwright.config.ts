import { defineConfig, devices } from '@playwright/test';
import { BASE_URL } from './support/env';

/**
 * FinAlly E2E configuration.
 *
 * Two ways to run (see README.md):
 *   1. Against the built container:  npm run test:docker
 *   2. Against a locally-running app: npm test   (defaults to http://localhost:8000)
 *
 * Override the target with E2E_BASE_URL.
 */
export default defineConfig({
  testDir: './e2e',

  /*
   * The app is single-user by design (PLAN.md §7: user_id is always "default").
   * One cash balance, one watchlist, one positions table — so tests mutate shared
   * state and MUST NOT run in parallel. This is not a performance oversight.
   */
  fullyParallel: false,
  workers: 1,

  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,

  /* A streaming app: prices arrive on a ~500ms cadence and the container boots
   * background tasks, so assertions need more headroom than Playwright's defaults. */
  timeout: 60_000,
  expect: { timeout: 15_000 },

  reporter: process.env.CI
    ? [['list'], ['html', { open: 'never' }], ['github']]
    : [['list'], ['html', { open: 'never' }]],

  use: {
    baseURL: BASE_URL,
    actionTimeout: 10_000,
    navigationTimeout: 30_000,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },

  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        /* PLAN.md §2: desktop-first, data-dense terminal layout. Test the wide
         * viewport the UI is actually designed for. */
        viewport: { width: 1600, height: 950 },
      },
    },
  ],
});

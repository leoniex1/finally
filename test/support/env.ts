/**
 * Single source of truth for where the suite points.
 *
 * - Docker run (docker-compose.test.yml) sets E2E_BASE_URL=http://app:8000
 * - Local run defaults to the port the container publishes and the backend
 *   serves on (PLAN.md §3: one container, one port, one origin).
 */
export const DEFAULT_BASE_URL = 'http://localhost:8000';

export const BASE_URL = process.env.E2E_BASE_URL?.replace(/\/+$/, '') || DEFAULT_BASE_URL;

/** True when running inside the Playwright container against the `app` service. */
export const IS_DOCKER_RUN = BASE_URL.includes('//app:');

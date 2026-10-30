import { defineConfig, devices } from '@playwright/test';

// Served from 127.0.0.1, not localhost: the API is at 127.0.0.1:8000, and only
// a same-site page gets the session cookies sent with its requests. From
// localhost the browser drops them and the app falls back to an in-memory
// token, so a reload would sign the test out.
const WEB = 'http://127.0.0.1:3000';
const API = 'http://127.0.0.1:8000';
const AUTH_STATE = 'e2e/.auth/admin.json';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: WEB,
    trace: 'retain-on-failure',
  },
  projects: [
    // Signs in through the real login page once and saves the session.
    { name: 'setup', testMatch: /auth\.setup\.ts/ },
    // Every spec here drives the real API with that session. Nothing is mocked.
    {
      name: 'chromium',
      dependencies: ['setup'],
      testIgnore: /fixtures\//,
      use: { ...devices['Desktop Chrome'], storageState: AUTH_STATE },
    },
    // Rendering tests against fixed payloads, kept apart and named as such so
    // a mocked test is never mistaken for an end-to-end one.
    {
      name: 'fixtures',
      testMatch: /fixtures\/.*\.spec\.ts/,
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: [
    {
      command: 'cd .. && uv run python -m uvicorn denoiser.api.main:app --host 127.0.0.1 --port 8000',
      url: `${API}/health`,
      timeout: 180 * 1000,
      reuseExistingServer: !process.env.CI,
    },
    {
      command: 'npm run dev -- -H 127.0.0.1 -p 3000',
      url: `${WEB}/login`,
      timeout: 180 * 1000,
      reuseExistingServer: !process.env.CI,
    },
  ],
});

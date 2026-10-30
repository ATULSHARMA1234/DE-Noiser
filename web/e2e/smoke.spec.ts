import { test, expect } from '@playwright/test';
import { watchForBreakage } from './guard';

// Every page, against the real API, signed in as the seeded admin. The mocked
// version of this answered every request with [] and so could not fail on
// anything the backend did.
const ROUTES = [
  '/app', '/app/explore', '/app/dashboards', '/app/incidents', '/app/issues', '/app/slos',
  '/app/metrics', '/app/monitors', '/app/traces', '/app/alerts', '/app/runbooks',
  '/app/topology', '/app/sources', '/app/integrations', '/app/notebooks', '/app/runs',
  '/app/live', '/app/audit', '/app/users', '/app/settings',
];

for (const route of ROUTES) {
  test(`${route} loads against the real API`, async ({ page }) => {
    const guard = watchForBreakage(page);
    await page.goto(route);
    await expect(page.locator('nav')).toBeVisible();
    await expect(page).not.toHaveURL(/\/login/);
    await page.waitForLoadState('networkidle');
    guard.assertClean();
  });
}

import { test, expect } from '@playwright/test';

test.describe('SemanticOS Smoke Tests', () => {
  const routes = [
    '/app',
    '/app/explore',
    '/app/dashboards',
    '/app/incidents',
    '/app/slos',
    '/app/metrics',
    '/app/traces',
    '/app/alerts',
    '/app/runbooks',
    '/app/topology',
    '/app/sources',
    '/app/integrations',
    '/app/audit',
    '/app/users',
    '/app/settings'
  ];

  for (const route of routes) {
    test(`Route ${route} loads without crashing`, async ({ page }) => {
      // Navigate to the route
      await page.addInitScript(() => {
        localStorage.setItem('token', 'test-token');
        localStorage.setItem('user', JSON.stringify({id:1,role:'ADMIN'}));
      localStorage.setItem('onboarding_completed', 'true');
      });
      await page.route('**/*', async (route) => {
        if (route.request().url().includes(':8000/auth/me')) { await route.fulfill({ status: 200, json: { id: 1, role: 'ADMIN', email: 'test@example.com' } }); } else if (route.request().url().includes(':8000/')) {
          await route.fulfill({ status: 200, json: [] });
        } else {
          await route.continue();
        }
      });
      await page.goto(route);
      
      // Verify the main layout has rendered
      await expect(page.locator('nav')).toBeVisible();
    });
  }
});

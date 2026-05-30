import { test, expect } from '@playwright/test';

test.describe('Alerts Page Features', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('token', 'test-token');
      localStorage.setItem('user', JSON.stringify({id:1,role:'ADMIN'}));
      localStorage.setItem('onboarding_completed', 'true');
    });
    await page.route('**/*', async (route) => {
      if (route.request().url().includes(':8000/auth/me')) {
        await route.fulfill({ status: 200, json: { id: 1, role: 'ADMIN', email: 'test@example.com' } });
      } else if (route.request().url().includes(':8000/alerts')) {
        await route.fulfill({ status: 200, json: { data: [] } });
      } else if (route.request().url().includes(':8000/')) {
        await route.fulfill({ status: 200, json: [] });
      } else {
        await route.continue();
      }
    });
    await page.goto('/app/alerts');
  });

  test('should render Alert History title', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Alert History' })).toBeVisible();
  });
});


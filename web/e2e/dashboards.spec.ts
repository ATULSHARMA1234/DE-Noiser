import { test, expect } from '@playwright/test';

test.describe('Dashboards Page Features', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('token', 'test-token');
      localStorage.setItem('user', JSON.stringify({id:1,role:'ADMIN'}));
      localStorage.setItem('onboarding_completed', 'true');
    });
    await page.route('**/*', async (route) => {
      if (route.request().url().includes(':8000/auth/me')) {
        await route.fulfill({ status: 200, json: { id: 1, role: 'ADMIN', email: 'test@example.com' } });
      } else if (route.request().url().includes(':8000/')) {
        await route.fulfill({ status: 200, json: [] });
      } else {
        await route.continue();
      }
    });
    await page.goto('/app/dashboards');
  });

  test('should render new dashboard button', async ({ page }) => {
    await expect(page.getByRole('button', { name: 'New Dashboard' })).toBeVisible();
  });

  test('should open create dashboard modal', async ({ page }) => {
    await page.getByRole('button', { name: 'New Dashboard' }).click();
    
    // Modal should appear
    await expect(page.getByRole('heading', { name: 'Create Dashboard' })).toBeVisible();
    await expect(page.getByPlaceholder('e.g. Production Overview')).toBeVisible();
    
    // Close modal
    await page.getByRole('button', { name: 'Cancel' }).click();
    await expect(page.getByRole('heading', { name: 'Create Dashboard' })).not.toBeVisible();
  });
});

import { test, expect } from '@playwright/test';

test.describe('Explore Page Features', () => {
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
    await page.goto('/app/explore');
  });

  test('should render search bar and examples', async ({ page }) => {
    await expect(page.locator('input[placeholder*="level:ERROR"]')).toBeVisible();
    await expect(page.getByText('level:ERROR', { exact: true }).first()).toBeVisible();
  });

  test('should click sample query and execute search', async ({ page }) => {
    // Click sample query
    await page.getByText('level:ERROR', { exact: true }).first().click();
    
    // Check if input was populated
    await expect(page.locator('input[placeholder*="level:ERROR"]')).toHaveValue('level:ERROR');
    
    // Click Run
    await page.getByRole('button', { name: 'Run', exact: true }).click();
    
    // Should show results or "No results found" but should not crash
    const resultsTable = page.locator('table');
    const noResults = page.getByText('No results found');
    
    // Wait for either results or no results
    await Promise.race([
      resultsTable.waitFor({ state: 'visible' }),
      noResults.waitFor({ state: 'visible' })
    ]);
  });
});

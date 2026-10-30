import { test, expect } from '@playwright/test';
import { watchForBreakage } from './guard';

const API = 'http://127.0.0.1:8000';

test('an organisation setting survives a reload; platform settings are read-only', async ({ page }) => {
  const guard = watchForBreakage(page);
  const before = (await (await page.request.get(`${API}/settings`)).json()).retention_days;
  const target = before === 45 ? 46 : 45;

  await page.goto('/app/settings');
  const retention = page.locator('input[type="number"]').first();
  await retention.fill(String(target));
  await page.getByRole('button', { name: /Save Changes/ }).click();
  await expect(page.getByRole('button', { name: /Saved!/ })).toBeVisible();

  await page.reload();
  await expect(page.locator('input[type="number"]').first()).toHaveValue(String(target));

  // The shared object store is the operator's, not this organisation's.
  await expect(page.locator('input.font-mono').first()).toBeDisabled();

  // Put it back for the next run.
  await page.request.put(`${API}/settings`, { data: { retention_days: before } });
  guard.assertClean();
});

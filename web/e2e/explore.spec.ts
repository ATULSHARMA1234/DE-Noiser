import { test, expect } from '@playwright/test';
import { watchForBreakage } from './guard';

test('a valid query runs; a malformed one says what is wrong', async ({ page }) => {
  const guard = watchForBreakage(page);
  await page.goto('/app/explore');
  const box = page.locator('input[placeholder*="level:ERROR"]');

  await box.fill('level:ERROR -level:DEBUG status:>=500');
  const ok = page.waitForResponse((r) => r.url().endsWith('/query') && r.request().method() === 'POST');
  await page.getByRole('button', { name: 'Run', exact: true }).click();
  expect((await ok).status()).toBe(200);

  await box.fill('(level:ERROR');
  const bad = page.waitForResponse((r) => r.url().endsWith('/query') && r.request().method() === 'POST');
  await page.getByRole('button', { name: 'Run', exact: true }).click();
  expect((await bad).status()).toBe(400);
  await expect(page.getByText(/never closed/).first()).toBeVisible();
  guard.assertClean();
});

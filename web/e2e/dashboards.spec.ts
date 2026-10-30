import { test, expect } from '@playwright/test';
import { watchForBreakage } from './guard';

const API = 'http://127.0.0.1:8000';

test('create, open and delete a dashboard', async ({ page }) => {
  const guard = watchForBreakage(page);
  const name = `e2e-${Date.now()}`;

  await page.goto('/app/dashboards');
  await page.getByRole('button', { name: 'New Dashboard' }).click();
  await page.getByPlaceholder('e.g. Production Overview').fill(name);
  await page.getByRole('button', { name: 'Create', exact: true }).click();
  await expect(page.getByText(name).first()).toBeVisible();

  // Stored by the API, not just shown.
  const stored = await (await page.request.get(`${API}/dashboards`)).json();
  const created = stored.find((d: { name: string }) => d.name === name);
  expect(created).toBeTruthy();

  await page.goto('/app/dashboards');
  const card = page.locator('div.group', { hasText: name });
  await card.hover();
  await card.locator('button').click();
  await page.getByRole('button', { name: 'Confirm' }).click();
  await expect(page.getByText(name)).toHaveCount(0);

  expect((await page.request.get(`${API}/dashboards/${created.id}`)).status()).toBe(404);
  guard.assertClean();
});

import { test, expect } from '@playwright/test';
import { watchForBreakage } from './guard';

test('provision, deactivate, reactivate and delete an operator', async ({ page }) => {
  const guard = watchForBreakage(page);
  const email = `e2e.${Date.now()}@semanticos.io`;

  await page.goto('/app/users');
  await page.getByRole('button', { name: 'Provision Operator' }).first().click();
  await page.getByPlaceholder('analyst.name@semanticos.io').fill(email);
  await page.locator('form input[type="password"]').fill('e2e-Operator-Passw0rd!');
  await page.locator('form select').selectOption('ANALYST');
  await page.locator('form button[type="submit"]').click();

  // The operator's card: the one tile holding this address and its actions.
  const card = page.locator('div.rounded-2xl', { hasText: email });
  await expect(page.getByText(email, { exact: true })).toBeVisible();

  await card.getByTitle('Deactivate operator').click();
  await page.getByRole('button', { name: 'Confirm' }).click();
  await expect(card.getByText('Inactive')).toBeVisible();

  await card.getByTitle('Reactivate operator').click();
  await page.getByRole('button', { name: 'Confirm' }).click();
  await expect(card.getByText('Inactive')).toHaveCount(0);

  await card.getByTitle('Delete operator context').click();
  await page.getByRole('button', { name: 'Confirm' }).click();
  await expect(page.getByText(email, { exact: true })).toHaveCount(0);
  guard.assertClean();
});

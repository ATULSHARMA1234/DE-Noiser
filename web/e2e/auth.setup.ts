import { test as setup, expect } from '@playwright/test';

// The admin the API seeds on first boot. CI sets SEMANTICOS_ADMIN_PASSWORD for
// the API and E2E_ADMIN_PASSWORD for this file to the same value.
const EMAIL = process.env.E2E_ADMIN_EMAIL ?? 'admin@semanticos.io';
const PASSWORD = process.env.E2E_ADMIN_PASSWORD;

setup('sign in through the real login page', async ({ page }) => {
  if (!PASSWORD) throw new Error('Set E2E_ADMIN_PASSWORD to the API\'s SEMANTICOS_ADMIN_PASSWORD');

  await page.goto('/login');
  await page.locator('input[type="email"]').fill(EMAIL);
  await page.locator('input[type="password"]').fill(PASSWORD);
  await page.locator('button[type="submit"]').first().click();

  await page.waitForURL('**/app');
  await expect(page.locator('nav')).toBeVisible();

  // A first sign-in opens the onboarding wizard over everything. Walk through
  // it the way a user does, so its completion is recorded by the app itself.
  const next = page.getByRole('button', { name: /^(Next|Get Started)/ });
  for (let step = 0; step < 6 && (await next.isVisible()); step++) {
    await next.click();
  }
  await expect(next).toHaveCount(0);
  await page.context().storageState({ path: 'e2e/.auth/admin.json' });
});

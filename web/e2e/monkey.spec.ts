import { test, expect } from '@playwright/test';

const ROUTES = [
  '/app',
  '/app/explore',
  '/app/alerts',
  '/app/traces'
];

test.describe('Monkey Crawler - Button Fuzzing', () => {
  for (const routePath of ROUTES) {
    test(`should click all buttons on ${routePath} without crashing`, async ({ page }) => {
      // Mock auth and state
      await page.addInitScript(() => {
        localStorage.setItem('token', 'test-token');
        localStorage.setItem('user', JSON.stringify({id: 1, role: 'ADMIN', email: 'test@example.com'}));
        localStorage.setItem('onboarding_completed', 'true');
      });

      page.on('console', msg => {
        if (msg.type() === 'error') {
          console.log(`BROWSER ERROR: ${msg.text()}`);
        }
      });
      page.on('pageerror', err => {
        console.log(`PAGE ERROR: ${err.message}`);
      });

      // Generic API mocking to prevent fetch errors
      await page.route('**/*', async (route) => {
        if (route.request().url().includes(':8000/auth/me')) {
          await route.fulfill({ status: 200, json: { id: 1, role: 'ADMIN', email: 'test@example.com' } });
        } else if (route.request().url().includes(':8000/alerts') || route.request().url().includes(':8000/incidents') || route.request().url().includes(':8000/search') || route.request().url().includes(':8000/explore')) {
          await route.fulfill({ status: 200, json: { data: [] } });
        } else if (route.request().url().includes(':8000/')) {
          await route.fulfill({ status: 200, json: [] });
        } else {
          await route.continue();
        }
      });

      // Visit page and wait for idle
      await page.goto(routePath, { waitUntil: 'networkidle' });

      // Find all buttons on the initial load
      const buttons = page.locator('button');
      const count = await buttons.count();
      
      console.log(`Found ${count} buttons on ${routePath}`);

      // We click each button one by one
      for (let i = 0; i < count; i++) {
        // Reload page to ensure clean state before clicking the next button.
        //
        // Tolerant of an in-flight navigation: the previous iteration may have
        // clicked something that navigates, and Playwright aborts a goto that is
        // interrupted by another navigation. That raced rather than failed on
        // Chromium and failed outright on WebKit, which is the kind of
        // difference that makes a suite look green until it does not.
        try {
          await page.goto(routePath, { waitUntil: 'domcontentloaded' });
        } catch {
          await page.waitForTimeout(300);
          await page.goto(routePath, { waitUntil: 'domcontentloaded' });
        }

        // Wait a small amount of time for React to attach handlers
        await page.waitForTimeout(500);

        const currentButton = page.locator('button').nth(i);

        if (!(await currentButton.isVisible()) || (await currentButton.isDisabled())) {
          continue;
        }

        const btnText = await currentButton.textContent();

        // Logging out is not a crash, it is the button working. Clicking it
        // ends the session for every following iteration and leaves the crawler
        // racing its own redirect to /login — so the control is skipped rather
        // than the failure being explained away later.
        if (/log ?out|sign ?out/i.test(btnText ?? '')) {
          console.log(`Skipping session-ending control on ${routePath}: "${btnText?.trim()}"`);
          continue;
        }
        console.log(`Clicking button ${i} on ${routePath} (Text: "${btnText?.trim()}")`);

        try {
          await currentButton.click({ timeout: 2000, force: true });
        } catch (e: any) {
          // If the click fails (e.g. element detached), we ignore and move on
          console.log(`Skipped button ${i} on ${routePath}: ${e.message}`);
          continue;
        }

        // Wait a brief moment for any crash to occur
        await page.waitForTimeout(500);

        const bodyContent = await page.locator('body').innerHTML();
        if (bodyContent.trim() === '') {
          throw new Error(`Clicking button ${i} on ${routePath} caused the page to blank out!`);
        }
      }
    });
  }
});

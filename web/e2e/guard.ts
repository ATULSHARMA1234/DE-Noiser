import { expect, type Page } from '@playwright/test';

/**
 * Fail the test on what a user would hit as breakage: any 5xx from the API and
 * any uncaught error in the page. Call before navigating; assert at the end.
 */
export function watchForBreakage(page: Page) {
  const problems: string[] = [];
  page.on('response', (res) => {
    if (res.url().startsWith('http://127.0.0.1:8000') && res.status() >= 500) {
      problems.push(`${res.status()} ${res.request().method()} ${res.url()}`);
    }
  });
  page.on('pageerror', (err) => problems.push(`page error: ${err.message}`));
  return {
    assertClean: () => expect(problems, problems.join('\n')).toEqual([]),
  };
}

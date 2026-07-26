import { test, expect } from '@playwright/test';

/**
 * The Command Center announces that a report exists; the report page is where
 * the analysis is read. It used to dump the entire narrative summary, every
 * remediation hint and every cluster into the dashboard panel.
 */

const RUN_ID = 'run-e2e-0001';

const RUN = {
  id: RUN_ID,
  source: 'data/production_failure.log',
  status: 'Completed',
  raw_lines: 300,
  cluster_count: 2,
  reduction_ratio: 0.98,
  duration_sec: 18.3,
  created_at: '2026-07-26T12:34:47+00:00',
  intelligence: {
    failure_domain: 'Application JVM (Memory Management)',
    incident_summary: 'A critical incident originated from severe resource exhaustion in a Java component.',
    root_cause_hints: ['Analyze JVM heap dumps', 'Review recent deployments'],
  },
  clusters_snapshot: [
    {
      cluster_id: 0, size: 240, summary: 'Database connection pool exhausted',
      representative_template: 'connection pool at <NUM>% capacity',
      representative_log: 'WARN pool at 60% capacity',
      anomaly_score: 0.82, priority: 'P1', projection_2d: [[0.1, 0.2], [0.3, 0.4]],
    },
    {
      cluster_id: -1, size: 12, summary: 'Analyzing...',
      representative_template: 'java.lang.OutOfMemoryError: Java heap space',
      representative_log: 'FATAL java.lang.OutOfMemoryError: Java heap space',
      anomaly_score: 0.95, priority: 'P0', projection_2d: [[1.1, 1.2]],
    },
  ],
};

async function stubApi(page: any) {
  await page.addInitScript(() => {
    localStorage.setItem('token', 'test-token');
    localStorage.setItem('user', JSON.stringify({ id: 1, role: 'ADMIN' }));
    localStorage.setItem('onboarding_completed', 'true');
  });
  await page.route('**/*', async (route: any) => {
    const url = route.request().url();
    if (url.includes(':8000/auth/me')) {
      await route.fulfill({ status: 200, json: { id: 1, role: 'ADMIN', email: 'test@example.com' } });
    } else if (url.includes(`:8000/runs/${RUN_ID}`) || url.includes(`:8000/analysis/runs/${RUN_ID}`)) {
      await route.fulfill({ status: 200, json: RUN });
    } else if (url.includes(':8000/analysis/runs') || url.includes(':8000/runs')) {
      await route.fulfill({ status: 200, json: [RUN] });
    } else if (url.includes(':8000/')) {
      await route.fulfill({ status: 200, json: [] });
    } else {
      await route.continue();
    }
  });
}

test.describe('Analysis report', () => {
  test.beforeEach(async ({ page }) => { await stubApi(page); });

  test('command center announces the report instead of printing it', async ({ page }) => {
    await page.goto('/app');

    await expect(page.getByText('Report generated')).toBeVisible();
    await expect(page.getByText('View full analysis report')).toBeVisible();

    // The narrative summary and the hint list belong to the report, not here.
    await expect(page.getByText(RUN.intelligence.incident_summary)).toHaveCount(0);
    await expect(page.getByText('Analyze JVM heap dumps')).toHaveCount(0);
  });

  test('the card links through to the full report', async ({ page }) => {
    await page.goto('/app');
    await page.getByText('View full analysis report').click();

    await expect(page).toHaveURL(new RegExp(`/app/runs/${RUN_ID}$`));
    await expect(page.getByRole('heading', { name: 'Analysis Report' })).toBeVisible();
  });

  test('the report carries the whole analysis', async ({ page }) => {
    await page.goto(`/app/runs/${RUN_ID}`);

    await expect(page.getByText(RUN.intelligence.failure_domain)).toBeVisible();
    await expect(page.getByText(RUN.intelligence.incident_summary)).toBeVisible();
    await expect(page.getByText('Analyze JVM heap dumps')).toBeVisible();
    await expect(page.getByText('Review recent deployments')).toBeVisible();

    // Every cluster, not a top-N slice.
    await expect(page.getByText('Database connection pool exhausted')).toBeVisible();
    await expect(page.getByText('java.lang.OutOfMemoryError: Java heap space').first()).toBeVisible();

    // A cluster whose summary never resolved falls back to its template.
    await expect(page.getByText('Analyzing...')).toHaveCount(0);
  });

  test('a missing run reports that rather than rendering an empty report', async ({ page }) => {
    // Registered after the catch-all in beforeEach, so this handler wins.
    // Scoped to the API port: the page URL (/app/runs/does-not-exist) contains
    // the same path, and matching it would serve JSON instead of the app.
    await page.route(
      (url) => url.port === '8000' && url.pathname.includes('/runs/does-not-exist'),
      async (route: any) => {
        await route.fulfill({ status: 404, json: { detail: 'Run not found' } });
      },
    );
    await page.goto('/app/runs/does-not-exist');
    await expect(page.getByText('Report unavailable')).toBeVisible();
  });

  test('the runs list links each run to its report', async ({ page }) => {
    await page.goto('/app/runs');
    await page.getByRole('button', { name: 'Report' }).first().click();
    await expect(page).toHaveURL(new RegExp(`/app/runs/${RUN_ID}$`));
  });
});

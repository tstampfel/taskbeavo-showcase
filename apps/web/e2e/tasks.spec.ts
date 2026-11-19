import { expect, test } from '@playwright/test';

/**
 * The public task pages (BEAV-91), server-rendered from the stub API (e2e/legal-api-stub.mjs).
 * They show the approximate area only (hard rule e) and list tasks newest first, the same for
 * everyone (hard rule k).
 */

const titles = {
  hr: 'Otvoreni zadaci',
  en: 'Open tasks',
  de: 'Offene Aufgaben',
  fr: 'Tâches ouvertes',
} as const;

for (const [locale, title] of Object.entries(titles)) {
  test(`/${locale}/tasks lists open tasks`, async ({ page }) => {
    const response = await page.goto(`/${locale}/tasks`);
    expect(response?.status()).toBe(200);
    await expect(page.getByRole('heading', { level: 1 })).toHaveText(title);
    await expect(page).toHaveTitle(new RegExp(title));
    await expect(page.locator('meta[name="description"]')).toHaveAttribute('content', /.+/);
    await expect(page.locator('link[rel="alternate"][hreflang="de"]')).toHaveAttribute(
      'href',
      /\/de\/tasks$/,
    );
    await expect(page.getByTestId('browse-task-42')).toBeVisible();
    // No horizontal scroll at 375px, also with the longer German and French chips.
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
  });
}

test('the browse list pages forward and filters by category', async ({ page }) => {
  await page.goto('/en/tasks');
  await expect(page.getByTestId('browse-list').getByRole('listitem')).toHaveCount(2);
  await page.getByTestId('browse-more').click();
  await expect(page).toHaveURL(/\/en\/tasks\?after=page2$/);
  await expect(page.getByTestId('browse-task-44')).toBeVisible();
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute('content', /noindex/);

  await page.getByTestId('browse-category-ERRANDS').click();
  await expect(page).toHaveURL(/category=ERRANDS/);
  await expect(page.getByTestId('browse-task-43')).toBeVisible();
  await expect(page.getByTestId('browse-task-42')).toHaveCount(0);
});

test('a task page shows the approximate area and nothing exact', async ({ page }) => {
  const response = await page.goto('/de/tasks/42');
  expect(response?.status()).toBe(200);
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(
    'Mow the lawn and trim the hedge',
  );
  await expect(page).toHaveTitle(/Mow the lawn and trim the hedge/);
  await expect(page.getByTestId('task-area')).toHaveText('Gegend: Dubrovnik');
  await expect(page.getByTestId('task-approximate-note')).toBeVisible();
  const jsonLd = JSON.parse(
    (await page.locator('script[type="application/ld+json"]').first().textContent()) ?? '{}',
  );
  expect(jsonLd['@type']).toBe('BreadcrumbList');
  expect(jsonLd.itemListElement).toHaveLength(3);
});

test('an unknown task is a 404', async ({ page }) => {
  const response = await page.goto('/en/tasks/999');
  expect(response?.status()).toBe(404);
});

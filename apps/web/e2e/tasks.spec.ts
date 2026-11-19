import { expect, test, type Answers } from './graphql-stub';

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

const poster = {
  __typename: 'User',
  id: 'user-1',
  email: 'poster@example.com',
  firstName: 'Ana',
  lastName: 'Primjer',
  locale: 'EN',
  country: 'DE',
  timeZone: 'Europe/Berlin',
  phoneVerified: true,
  phoneNumberMasked: null,
};

function signedIn(isMine: boolean): Answers {
  return {
    Viewer: () => ({ data: { viewer: poster } }),
    TermsStatus: () => ({
      data: {
        viewer: { __typename: 'User', id: poster.id, mustAcceptTerms: false, termsToAccept: null },
      },
    }),
    TaskListing: ({ id }) => ({
      data: {
        taskListing: {
          __typename: 'TaskListing',
          id,
          category: 'GARDEN',
          title: 'Mow the lawn and trim the hedge',
          description: null,
          approximateLat: 42.66,
          approximateLng: 18.09,
          serviceAreaName: 'Dubrovnik',
          windowStart: null,
          windowEnd: null,
          flexibleDate: true,
          budgetCents: 3500,
          photos: [],
          isMine,
          publishedAt: new Date().toISOString(),
        },
      },
    }),
  };
}

// Offer emails link a web-only poster to the task's public page: from there, their offers.
test('the signed-in poster gets a way to their offers', async ({ page, stubGraphql }) => {
  const stub = await stubGraphql(signedIn(true));
  await page.goto('/en/tasks/42');
  const link = page.getByTestId('see-offers');
  await expect(link).toHaveText('See offers');
  await expect(link).toHaveAttribute('href', '/en/tasks/42/offers');
  expect(stub.calls.find((call) => call.name === 'TaskListing')?.variables).toEqual({ id: '42' });
});

test('other visitors get no offers link', async ({ page, stubGraphql }) => {
  await stubGraphql(signedIn(false));
  await page.goto('/en/tasks/42');
  await expect(page.getByTestId('task-page')).toBeVisible();
  await expect(page.getByTestId('site-header')).toBeVisible();
  await expect(page.getByTestId('see-offers')).toHaveCount(0);
});

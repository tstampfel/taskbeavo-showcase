import { expect, test } from '@playwright/test';

const taglines = {
  hr: 'Lokalna pomoć u Hrvatskoj',
  en: 'Local help in Croatia',
  de: 'Hilfe vor Ort in Kroatien',
  fr: 'De l’aide locale en Croatie',
} as const;

for (const [browserLocale, expected] of [
  ['it-IT', 'hr'],
  ['de-AT', 'de'],
  ['fr-CH', 'fr'],
] as const) {
  test(`/ redirects a ${browserLocale} browser to /${expected}`, async ({ browser }) => {
    const context = await browser.newContext({ locale: browserLocale });
    try {
      const page = await context.newPage();
      await page.goto('/');
      await expect(page).toHaveURL(new RegExp(`/${expected}$`));
      await expect(page.getByTestId('home')).toBeVisible();
    } finally {
      await context.close();
    }
  });
}

for (const [locale, tagline] of Object.entries(taglines)) {
  test(`home page renders in ${locale}`, async ({ page }) => {
    await page.goto(`/${locale}`);
    await expect(page.locator('html')).toHaveAttribute('lang', locale);
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('TaskBeavo');
    await expect(page.getByTestId('home-tagline')).toHaveText(tagline);
    await expect(page).toHaveTitle('TaskBeavo');
    await expect(page.locator('meta[name="description"]')).toHaveAttribute('content', tagline);
    await expect(page.locator('link[rel="canonical"]')).toHaveAttribute(
      'href',
      `http://localhost:3100/${locale}`,
    );
    for (const alt of Object.keys(taglines)) {
      await expect(page.locator(`link[rel="alternate"][hreflang="${alt}"]`)).toHaveAttribute(
        'href',
        `http://localhost:3100/${alt}`,
      );
    }
    await expect(page.locator('link[rel="alternate"][hreflang="x-default"]')).toHaveAttribute(
      'href',
      'http://localhost:3100/hr',
    );
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    );
    expect(overflow).toBe(false);
  });
}

test('an unknown page under a locale is a translated 404', async ({ page }) => {
  const response = await page.goto('/de/gibt-es-nicht');
  expect(response?.status()).toBe(404);
  await expect(page.locator('html')).toHaveAttribute('lang', 'de');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Seite nicht gefunden');
  await expect(page.getByRole('link', { name: 'Zurück zur Startseite' })).toHaveAttribute(
    'href',
    '/de',
  );
});

test('an unsupported locale is redirected to Croatian, then 404s there', async ({ browser }) => {
  const context = await browser.newContext({ locale: 'it-IT' });
  try {
    const page = await context.newPage();
    const response = await page.goto('/it');
    expect(response?.request().redirectedFrom()?.url()).toMatch(/\/it$/);
    expect(page.url()).toMatch(/\/hr\/it$/);
    expect(response?.status()).toBe(404);
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Stranica nije pronađena');
  } finally {
    await context.close();
  }
});

test('a path the proxy skips gets the Croatian global 404, not a [locale] page', async ({
  page,
}) => {
  // Paths with a dot bypass the proxy; dynamicParams = false keeps "x.y" out of [locale].
  const response = await page.goto('/x.y');
  expect(response?.status()).toBe(404);
  await expect(page.locator('html')).toHaveAttribute('lang', 'hr');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Stranica nije pronađena');
});

test('no locale cookie is stored by the browser-language redirect or by visiting a locale page', async ({
  browser,
}) => {
  // No consent banner at launch: the cookie must only follow an active language pick.
  const context = await browser.newContext({ locale: 'de-AT' });
  try {
    const page = await context.newPage();
    await page.goto('/');
    await expect(page).toHaveURL(/\/de$/);
    await page.goto('/fr');
    await expect(page.getByTestId('home-tagline')).toHaveText(taglines.fr);
    expect((await context.cookies()).map((c) => c.name)).not.toContain('NEXT_LOCALE');
  } finally {
    await context.close();
  }
});

test('the language switcher opens the same page in another language and remembers it', async ({
  page,
  context,
}) => {
  await page.goto('/hr');
  const switcher = page.getByRole('navigation', { name: 'Jezik' });
  await expect(switcher.getByRole('link', { name: 'Hrvatski' })).toHaveAttribute(
    'aria-current',
    'true',
  );

  // Signed in, this saves the choice on the account (signed out the API refuses it harmlessly).
  const saved = page.waitForRequest(
    (request) =>
      request.url().endsWith('/api/graphql') && request.postData()?.includes('SetLocale') === true,
  );
  await switcher.getByRole('link', { name: 'Deutsch' }).click();
  expect((await saved).postDataJSON().variables).toEqual({ locale: 'DE' });

  await expect(page).toHaveURL(/\/de$/);
  await expect(page.locator('html')).toHaveAttribute('lang', 'de');
  await expect(page.getByTestId('home-tagline')).toHaveText(taglines.de);
  await expect(
    page.getByRole('navigation', { name: 'Sprache' }).getByRole('link', { name: 'Deutsch' }),
  ).toHaveAttribute('aria-current', 'true');
  const cookie = (await context.cookies()).find((c) => c.name === 'NEXT_LOCALE');
  expect(cookie?.value).toBe('de');
  // A year, not a session cookie.
  expect(cookie?.expires ?? 0).toBeGreaterThan(Date.now() / 1000 + 300 * 24 * 3600);

  await page.goto('/');
  await expect(page).toHaveURL(/\/de$/);
});

test('the language switcher keeps the page path and query string', async ({ page }) => {
  await page.goto('/fr/gibt-es-nicht?category=garden&tag=a&tag=b');
  await expect(
    page.getByRole('navigation', { name: 'Langue' }).getByRole('link', { name: 'English' }),
  ).toHaveAttribute('href', '/en/gibt-es-nicht?category=garden&tag=a&tag=b');
});

test('the language switcher keeps the query string on a statically rendered page', async ({
  page,
}) => {
  await page.goto('/hr?ref=newsletter');
  await expect(
    page.getByRole('navigation', { name: 'Jezik' }).getByRole('link', { name: 'Deutsch' }),
  ).toHaveAttribute('href', '/de?ref=newsletter');
});

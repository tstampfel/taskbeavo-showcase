import { expect, test } from '@playwright/test';

// The real validators (Apple's AASA validator, Google's Digital Asset Links API) need the
// deployed domain and signed builds; these check what they check on the files we serve.
test('apple-app-site-association is JSON at its exact path, without a redirect', async ({
  request,
}) => {
  const response = await request.get('/.well-known/apple-app-site-association', {
    maxRedirects: 0,
  });
  expect(response.status()).toBe(200);
  expect(response.headers()['content-type']).toBe('application/json');
  const body = await response.json();
  expect(body.applinks.details[0].appIDs).toContain('TESTTEAM01.com.taskbeavo.app');
  const paths = body.applinks.details[0].components.map((c: { '/': string }) => c['/']);
  expect(paths).toEqual(expect.arrayContaining(['/bookings/*', '/de/tasks/*', '/helper/*']));
});

test('assetlinks.json is JSON at its exact path, without a redirect', async ({ request }) => {
  const response = await request.get('/.well-known/assetlinks.json', { maxRedirects: 0 });
  expect(response.status()).toBe(200);
  expect(response.headers()['content-type']).toBe('application/json');
  const [statement] = await response.json();
  expect(statement.relation).toEqual(['delegate_permission/common.handle_all_urls']);
  expect(statement.target.package_name).toBe('com.taskbeavo.app');
  expect(statement.target.sha256_cert_fingerprints).toHaveLength(1);
});

for (const [path, title] of [
  ['/de/tasks/42', 'Aufgabe'],
  ['/fr/bookings/7/release', 'Débloquer le paiement'],
  ['/hr/bookings/7/no-access', 'Pomagač nije mogao ući'],
  ['/en/conversations/3', 'Messages'],
  ['/de/helper/payouts', 'Auszahlungen'],
  ['/fr/helper/payouts/return', 'Versements'],
  ['/hr/helper/payouts/refresh', 'Isplate'],
] as const) {
  test(`${path} opens its page on the web, unindexed`, async ({ page }) => {
    const response = await page.goto(path);
    expect(response?.status()).toBe(200);
    await expect(page.getByRole('heading', { level: 1 })).toHaveText(title);
    await expect(page.locator('meta[name="robots"]')).toHaveAttribute(
      'content',
      'noindex, nofollow',
    );
  });
}

test('a link without a locale goes to the visitor’s language', async ({ browser }) => {
  const context = await browser.newContext({ locale: 'de-AT' });
  try {
    const page = await context.newPage();
    await page.goto('/bookings/7/release?confirm=1');
    await expect(page).toHaveURL(/\/de\/bookings\/7\/release\?confirm=1$/);
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Zahlung freigeben');
    // Only a link home: the page has no button that could release anything.
    await expect(page.getByRole('main').getByRole('button')).toHaveCount(0);
  } finally {
    await context.close();
  }
});

test('a malformed ID is a 404', async ({ page }) => {
  const response = await page.goto('/en/bookings/not%20an%20id/release');
  expect(response?.status()).toBe(404);
});

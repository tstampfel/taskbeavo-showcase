import { expect, test } from '@playwright/test';

const connected = {
  hr: 'Usluga je povezana (verzija API-ja 9.9.9)',
  en: 'Service connected (API version 9.9.9)',
  de: 'Dienst verbunden (API-Version 9.9.9)',
  fr: 'Service connecté (version de l’API 9.9.9)',
} as const;

for (const [locale, text] of Object.entries(connected)) {
  test(`the sample ApiVersion query renders through Apollo in ${locale}`, async ({ page }) => {
    let operationName: string | undefined;
    await page.route('**/api/graphql', async (route) => {
      operationName = route.request().postDataJSON()?.operationName;
      await route.fulfill({ json: { data: { apiVersion: '9.9.9' } } });
    });

    await page.goto(`/${locale}`);

    await expect(page.getByTestId('api-status')).toHaveText(text);
    expect(operationName).toBe('ApiVersion');
  });
}

test('shows a translated message when the API is unreachable', async ({ page }) => {
  await page.route('**/api/graphql', (route) => route.abort());

  await page.goto('/de');

  await expect(page.getByTestId('api-status')).toHaveText(
    'Der Dienst ist gerade nicht erreichbar.',
  );
});

test('/api/graphql is a route handler, not redirected to a locale', async ({
  request,
  baseURL,
}) => {
  const response = await request.post('/api/graphql', {
    data: { query: '{ apiVersion }' },
    headers: {
      origin: new URL(baseURL ?? 'http://localhost:3000').origin,
      'x-taskbeavo-csrf': '1',
    },
    maxRedirects: 0,
  });

  // Without a backend running the handler answers 502; either way it must not be a redirect.
  expect(response.status()).not.toBe(307);
  expect(response.headers()['location']).toBeUndefined();
});

test('/api/graphql refuses a request without the CSRF header', async ({ request }) => {
  const response = await request.post('/api/graphql', {
    data: { query: '{ apiVersion }' },
    maxRedirects: 0,
  });

  expect(response.status()).toBe(403);
  expect((await response.json()).errors[0].extensions.code).toBe('REQUEST_REJECTED');
});

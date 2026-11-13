import type { Page, Request } from '@playwright/test';
import { expect, test, type Answers, type Variables } from './graphql-stub';

/**
 * The cookie banner and Settings → Privacy and data (BEAV-83) against a stubbed API. Every
 * answer is sent as setConsent before it is stored. The e2e server has a dummy PostHog project
 * whose host this spec routes (playwright.config.ts), so it sees that nothing reaches PostHog
 * and nothing of PostHog's is stored before a yes or after a no, and that a yes does reach it.
 */

// English strings from packages/i18n (Playwright can't import the JSON catalogues).
const en = {
  title: 'Cookies on TaskBeavo',
  settingsTitle: 'Cookie settings',
  saveFailed: "Your choice couldn't be saved. Please try again.",
};

const signedOut: Answers = {
  Viewer: () => ({ data: { viewer: null } }),
  ApiVersion: () => ({ data: { apiVersion: 'test' } }),
};

const consentState = (purpose: string, granted: boolean) => ({
  __typename: 'ConsentState',
  id: purpose,
  purpose,
  granted,
  version: granted ? 'cookies-1' : null,
  givenAt: '2026-11-13T10:00:00Z',
});

const setConsent = (variables: Variables) => ({
  data: {
    setConsent: consentState(String(variables.purpose), variables.granted === true),
  },
});

// NEXT_PUBLIC_POSTHOG_HOST of the e2e server (playwright.config.ts); nothing listens there.
const POSTHOG_HOST = 'http://localhost:3197';

/** Answers every request to the dummy PostHog host, and records it. */
async function watchPostHog(page: Page): Promise<Request[]> {
  const seen: Request[] = [];
  await page.route(`${POSTHOG_HOST}/**`, async (route) => {
    seen.push(route.request());
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
  });
  return seen;
}

/** The names of PostHog's cookies and localStorage keys in this browser. */
async function postHogStorage(page: Page): Promise<string[]> {
  const cookies = (await page.context().cookies())
    .map(({ name }) => name)
    .filter((name) => /^_{0,2}ph_/.test(name));
  const keys = await page.evaluate(() =>
    Object.keys(window.localStorage).filter((key) => /^_{0,2}ph_/.test(key)),
  );
  return [...cookies, ...keys];
}

async function storedChoice(page: Page) {
  const cookie = (await page.context().cookies()).find(({ name }) => name === 'tb_consent');
  return cookie ? (JSON.parse(decodeURIComponent(cookie.value)) as Record<string, unknown>) : null;
}

test('a first visit shows the banner, with no analytics before an answer', async ({
  page,
  stubGraphql,
}) => {
  const posthog = await watchPostHog(page);
  const stub = await stubGraphql(signedOut);
  await page.goto('/en');

  const banner = page.getByTestId('consent-banner');
  await expect(banner.getByRole('heading', { name: en.title })).toBeVisible();
  await expect(page.getByTestId('home')).toBeVisible();
  expect(stub.calls.map((call) => call.name)).not.toContain('SetConsent');
  expect(await storedChoice(page)).toBeNull();
  // PostHog would load and send asynchronously; let the page settle before looking.
  await page.waitForLoadState('networkidle');
  expect(posthog).toEqual([]);
  expect(await postHogStorage(page)).toEqual([]);
});

test('Reject logs the refusal, then the banner stays away', async ({ page, stubGraphql }) => {
  const posthog = await watchPostHog(page);
  const stub = await stubGraphql({ ...signedOut, SetConsent: setConsent });
  await page.goto('/en');

  await page.getByTestId('consent-reject').click();

  await expect(page.getByTestId('consent-banner')).toHaveCount(0);
  const sent = stub.calls.find((call) => call.name === 'SetConsent')?.variables;
  expect(sent).toMatchObject({ purpose: 'ANALYTICS', granted: false, language: 'EN' });
  expect(sent?.visitorId).toMatch(/^[0-9a-f-]{36}$/);
  expect(await storedChoice(page)).toEqual({
    analytics: false,
    visitorId: sent?.visitorId,
    version: null,
  });

  await page.reload();
  await expect(page.getByTestId('home')).toBeVisible();
  await expect(page.getByTestId('consent-banner')).toHaveCount(0);
  expect(posthog).toEqual([]);
  expect(await postHogStorage(page)).toEqual([]);
});

test('Accept starts PostHog; withdrawing stops it and deletes what it stored', async ({
  page,
  stubGraphql,
}) => {
  const posthog = await watchPostHog(page);
  const stub = await stubGraphql({ ...signedOut, SetConsent: setConsent });
  await page.goto('/de');
  expect(posthog).toEqual([]);

  await page.getByTestId('consent-accept').click();
  await expect(page.getByTestId('consent-banner')).toHaveCount(0);
  const first = stub.calls.filter((call) => call.name === 'SetConsent')[0]?.variables;
  expect(first).toMatchObject({ purpose: 'ANALYTICS', granted: true, language: 'DE' });
  expect(await storedChoice(page)).toMatchObject({ analytics: true, version: 'cookies-1' });
  // Only after the logged yes: PostHog is contacted and keeps its anonymous id.
  await expect.poll(() => posthog.length).toBeGreaterThan(0);
  await expect.poll(() => postHogStorage(page)).not.toEqual([]);

  // The footer reopens the switch, showing the stored yes; switching it off is logged too.
  await page.getByTestId('footer-cookie-settings').click();
  const analytics = page.getByTestId('consent-analytics');
  await expect(analytics).toBeChecked();
  await analytics.uncheck();
  await page.getByTestId('consent-save').click();

  await expect(page.getByTestId('consent-banner')).toHaveCount(0);
  const second = stub.calls.filter((call) => call.name === 'SetConsent')[1]?.variables;
  expect(second).toMatchObject({
    purpose: 'ANALYTICS',
    granted: false,
    visitorId: first?.visitorId,
  });
  expect(await storedChoice(page)).toMatchObject({ analytics: false });
  await expect.poll(() => postHogStorage(page)).toEqual([]);

  // Later visits send nothing and store nothing.
  const before = posthog.length;
  await page.reload();
  await expect(page.getByTestId('home')).toBeVisible();
  await page.goto('/de/fees');
  expect(posthog.length).toBe(before);
  expect(await postHogStorage(page)).toEqual([]);
});

test('a page load after a no deletes what PostHog stored on an earlier visit', async ({
  page,
  stubGraphql,
}) => {
  const posthog = await watchPostHog(page);
  await stubGraphql(signedOut);
  const visitorId = '6f1c9a8e-2d4b-4c1e-9a77-0b5e3c2d1f00';
  // A browser that said no, but still holds PostHog's id from a visit before the withdrawal.
  await page.context().addCookies([
    {
      name: 'tb_consent',
      value: encodeURIComponent(JSON.stringify({ analytics: false, visitorId, version: null })),
      url: 'http://localhost:3100',
    },
    {
      name: 'ph_phc_e2e_posthog',
      value: '%7B%22distinct_id%22%3A%22anon-1%22%7D',
      url: 'http://localhost:3100',
    },
  ]);
  await page.addInitScript(() => {
    if (!sessionStorage.getItem('seeded')) {
      sessionStorage.setItem('seeded', '1');
      localStorage.setItem('ph_phc_e2e_posthog', '{"distinct_id":"anon-1"}');
    }
  });

  await page.goto('/en');

  await expect(page.getByTestId('home')).toBeVisible();
  await expect(page.getByTestId('consent-banner')).toHaveCount(0);
  await expect.poll(() => postHogStorage(page)).toEqual([]);
  expect(posthog).toEqual([]);
});

test('Settings in the banner starts with analytics off', async ({ page, stubGraphql }) => {
  const stub = await stubGraphql({ ...signedOut, SetConsent: setConsent });
  await page.goto('/en');

  await page.getByTestId('consent-settings').click();
  await expect(page.getByRole('heading', { name: en.settingsTitle })).toBeVisible();
  await expect(page.getByTestId('consent-analytics')).not.toBeChecked();
  await page.getByTestId('consent-save').click();

  await expect(page.getByTestId('consent-banner')).toHaveCount(0);
  expect(stub.calls.find((call) => call.name === 'SetConsent')?.variables).toMatchObject({
    granted: false,
  });
});

test('a choice the server refused is not stored, and the banner says so', async ({
  page,
  stubGraphql,
}) => {
  await stubGraphql({
    ...signedOut,
    SetConsent: () => ({
      data: null,
      errors: [{ message: 'refused', extensions: { code: 'CONSENT_UNAVAILABLE' } }],
    }),
  });
  await page.goto('/en');

  await page.getByTestId('consent-accept').click();

  await expect(page.getByTestId('consent-banner').getByRole('alert')).toHaveText(en.saveFailed);
  expect(await storedChoice(page)).toBeNull();
});

test('Settings switches news and offers by email on and logs it', async ({ page, stubGraphql }) => {
  const viewer = {
    __typename: 'User',
    id: 'user-1',
    email: 'poster@example.com',
    firstName: 'Ana',
    lastName: 'Primjer',
    locale: 'en',
    country: 'DE',
    timeZone: 'Europe/Berlin',
    phoneVerified: true,
    phoneNumberMasked: null,
  };
  const stub = await stubGraphql({
    ApiVersion: () => ({ data: { apiVersion: 'test' } }),
    Viewer: () => ({ data: { viewer } }),
    TermsStatus: () => ({
      data: {
        viewer: { __typename: 'User', id: viewer.id, mustAcceptTerms: false, termsToAccept: null },
      },
    }),
    PosterBusiness: () => ({
      data: { viewer: { __typename: 'User', id: viewer.id, posterBusiness: null } },
    }),
    Consents: () => ({
      data: {
        consents: ['ANALYTICS', 'MARKETING_EMAIL', 'MARKETING_PUSH'].map((purpose) =>
          consentState(purpose, false),
        ),
      },
    }),
    SetConsent: setConsent,
  });
  await page.goto('/en/settings');

  const email = page.getByTestId('privacy-marketing-email');
  await expect(email).not.toBeChecked();
  await expect(page.getByTestId('privacy-marketing-push')).not.toBeChecked();
  await expect(page.getByTestId('privacy-analytics')).not.toBeChecked();
  await email.click();

  await expect(email).toBeChecked();
  expect(stub.calls.find((call) => call.name === 'SetConsent')?.variables).toEqual({
    purpose: 'MARKETING_EMAIL',
    granted: true,
    language: 'EN',
  });
});

import type { Page, Request } from '@playwright/test';
import { expect, test, type Answers, type Variables } from './graphql-stub';

/**
 * The cookie banner and Settings → Privacy and data (BEAV-83) against a stubbed API. Every
 * answer is sent as setConsent before it is stored, and nothing reaches PostHog before a yes
 * (src/consent/consent.test.ts proves posthog-js isn't initialised until then). The dev
 * server serves no PostHog key, so this spec can't see a yes reach PostHog either.
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

/** Every request to a PostHog host the page makes (not the app's own posthog-js chunk). */
function watchPostHog(page: Page): Request[] {
  const seen: Request[] = [];
  page.on('request', (request) => {
    if (/(^|\.)posthog\.com$/i.test(new URL(request.url()).hostname)) seen.push(request);
  });
  return seen;
}

async function storedChoice(page: Page) {
  const cookie = (await page.context().cookies()).find(({ name }) => name === 'tb_consent');
  return cookie ? (JSON.parse(decodeURIComponent(cookie.value)) as Record<string, unknown>) : null;
}

test('a first visit shows the banner, with no analytics before an answer', async ({
  page,
  stubGraphql,
}) => {
  const posthog = watchPostHog(page);
  const stub = await stubGraphql(signedOut);
  await page.goto('/en');

  const banner = page.getByTestId('consent-banner');
  await expect(banner.getByRole('heading', { name: en.title })).toBeVisible();
  await expect(page.getByTestId('home')).toBeVisible();
  expect(stub.calls.map((call) => call.name)).not.toContain('SetConsent');
  expect(await storedChoice(page)).toBeNull();
  expect(posthog).toEqual([]);
});

test('Reject logs the refusal, then the banner stays away', async ({ page, stubGraphql }) => {
  const posthog = watchPostHog(page);
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
});

test('Accept is logged with the same visitor id the footer link later reuses', async ({
  page,
  stubGraphql,
}) => {
  const stub = await stubGraphql({ ...signedOut, SetConsent: setConsent });
  await page.goto('/de');

  await page.getByTestId('consent-accept').click();
  await expect(page.getByTestId('consent-banner')).toHaveCount(0);
  const first = stub.calls.filter((call) => call.name === 'SetConsent')[0]?.variables;
  expect(first).toMatchObject({ purpose: 'ANALYTICS', granted: true, language: 'DE' });
  expect(await storedChoice(page)).toMatchObject({ analytics: true, version: 'cookies-1' });

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

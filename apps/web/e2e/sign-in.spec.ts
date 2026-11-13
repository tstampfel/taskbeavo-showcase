import { expect, test, type Page, type Route } from '@playwright/test';

/**
 * The sign-in, sign-up and phone screens against a stubbed API: the browser's calls to
 * /api/graphql and /api/auth/* are answered here. The route handlers themselves (cookies,
 * CSRF, token stripping) are covered by src/app/api/routes.test.ts.
 */

const user = {
  __typename: 'User',
  id: 'u1',
  email: 'ana.kovac@example.org',
  firstName: 'Ana',
  lastName: 'Kovač',
  locale: 'DE',
  country: 'DE',
  timeZone: 'Europe/Berlin',
  phoneVerified: false,
  phoneNumberMasked: null as string | null,
};

type Answer = { data?: unknown; errors?: unknown[] };

interface Api {
  viewer: typeof user | null;
  /** Operation names and variables sent to /api/graphql, in order. */
  operations: { name: string; variables: Record<string, unknown> }[];
  /** Bodies posted to /api/auth/<name>. */
  auth: Record<string, Record<string, unknown>[]>;
}

function codeError(code: string, extensions: Record<string, unknown> = {}): Answer {
  return { data: null, errors: [{ message: code, extensions: { code, ...extensions } }] };
}

/**
 * Stubs the API. `graphql` and `auth` may override answers by operation or handler name;
 * an override function is called with the variables or body.
 */
async function stubApi(
  page: Page,
  {
    graphql = {},
    auth = {},
  }: {
    graphql?: Record<string, (variables: Record<string, unknown>, api: Api) => Answer>;
    auth?: Record<string, (body: Record<string, unknown>, api: Api) => Answer>;
  } = {},
): Promise<Api> {
  const api: Api = { viewer: null, operations: [], auth: {} };
  // Never Google's real script: the dev server has a placeholder client ID.
  await stubGoogle(page);
  const defaults: Record<string, (variables: Record<string, unknown>) => Answer> = {
    Viewer: () => ({ data: { viewer: api.viewer } }),
    ApiVersion: () => ({ data: { apiVersion: 'test' } }),
    RequestEmailCode: () => ({ data: { requestEmailCode: true } }),
    RequestSignInNonce: () => ({
      data: {
        requestSignInNonce: {
          __typename: 'SignInNonce',
          id: 'n1',
          nonce: 'nonce-1',
          expiresAt: '2030-01-01T00:00:00Z',
        },
      },
    }),
    CurrentTerms: (variables) => ({
      data: {
        currentLegalDocument: {
          __typename: 'LegalDocument',
          id: `terms-${String(variables.language)}`,
          kind: 'TERMS',
          version: '2026-11',
          language: variables.language,
          effectiveAt: '2026-11-01T00:00:00Z',
        },
      },
    }),
    SetLocale: (variables) => ({
      data: { setLocale: { __typename: 'User', id: 'u1', locale: variables.locale } },
    }),
  };
  await page.route('**/api/graphql', async (route: Route) => {
    const body = route.request().postDataJSON() as {
      operationName: string;
      variables?: Record<string, unknown>;
    };
    const variables = body.variables ?? {};
    api.operations.push({ name: body.operationName, variables });
    const answer = (graphql[body.operationName] ?? defaults[body.operationName])?.(variables, api);
    await route.fulfill({ json: answer ?? { data: null } });
  });
  await page.route('**/api/auth/*', async (route: Route) => {
    const name = new URL(route.request().url()).pathname.split('/').pop()!;
    const body = route.request().postDataJSON() as Record<string, unknown>;
    (api.auth[name] ??= []).push(body);
    const answer = auth[name]?.(body, api) ?? { data: null };
    await route.fulfill({ json: answer });
  });
  return api;
}

function signedIn(api: Api, key: string, overrides: Partial<typeof user> = {}): Answer {
  api.viewer = { ...user, ...overrides };
  return { data: { [key]: { __typename: 'AuthSession', id: 's1', user: api.viewer } } };
}

async function expectNoTokensInStorage(page: Page) {
  const stored = await page.evaluate(() => ({
    local: Object.entries(window.localStorage),
    session: Object.entries(window.sessionStorage),
    cookies: document.cookie,
  }));
  // Only the explicit language pick may be kept, never a token.
  for (const [key] of stored.local) expect(key).toBe('taskbeavo.pickedLocale');
  expect(stored.session).toEqual([]);
  expect(stored.cookies).not.toMatch(/tb_access|tb_refresh/);
}

test('signs up with an email code, then verifies the phone', async ({ page }) => {
  const api = await stubApi(page, {
    auth: {
      'verify-email-code': (body) =>
        body.code === '111111'
          ? codeError('INVALID_CODE')
          : {
              data: {
                verifyEmailCode: {
                  __typename: 'AccountDetailsRequired',
                  id: 't1',
                  email: 'ana.kovac@example.org',
                  ticket: 'ticket-1',
                },
              },
            },
      'complete-account-details': (_body, a) => signedIn(a, 'completeAccountDetails'),
    },
    graphql: {
      RequestPhoneCode: (variables) =>
        String(variables.phone).startsWith('+1')
          ? codeError('PHONE_COUNTRY_NOT_SUPPORTED')
          : {
              data: {
                requestPhoneCode: {
                  __typename: 'PhoneCodeSent',
                  id: 'p1',
                  phoneNumberMasked: '+49 ••• 789',
                  expiresAt: '2030-01-01T00:00:00Z',
                  resendAvailableAt: '2000-01-01T00:00:00Z',
                },
              },
            },
      VerifyPhoneCode: (_variables, a) => {
        a.viewer = { ...user, phoneVerified: true, phoneNumberMasked: '+49 ••• 789' };
        return { data: { verifyPhoneCode: a.viewer } };
      },
    },
  });

  await page.goto('/en/sign-in');
  await expect(page).toHaveTitle('Log in or sign up · TaskBeavo');
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute('content', 'noindex, nofollow');
  await page.getByTestId('email').fill('ana.kovac@example.org');
  await page.getByTestId('send-code').click();

  expect(api.operations.find((o) => o.name === 'RequestEmailCode')?.variables).toEqual({
    email: 'ana.kovac@example.org',
    locale: 'EN',
  });
  await expect(page.getByTestId('email-code')).toContainText('We sent it to ana.kovac@example.org');
  await expect(page.getByTestId('email-code')).toContainText(/Resend code in 0:[0-5]\d/);

  // A wrong code says so and clears the field.
  await page.getByTestId('email-code-input').fill('111111');
  await expect(page.getByRole('main').getByRole('alert')).toHaveText(
    'That code is wrong or has expired. Check it, or ask for a new code.',
  );
  await page.getByTestId('email-code-input').fill('482107');

  // New account: both boxes start unticked and "Create account" waits for them.
  const details = page.getByTestId('account-details');
  await expect(details.getByRole('heading', { level: 1 })).toHaveText('Create your account');
  await page.getByTestId('first-name').fill('Ana');
  await page.getByTestId('last-name').fill('Kovač');
  await page.getByTestId('country').selectOption('DE');
  await details.getByText('DE', { exact: true }).click();
  await expect(page.getByTestId('accept-terms')).not.toBeChecked();
  await expect(page.getByTestId('create-account')).toBeDisabled();
  await expect(details).toContainText('Tick the Terms box to continue');
  await page.getByTestId('is-adult').check();
  await expect(page.getByTestId('create-account')).toBeDisabled();
  // The terms open in the language picked for the account, the one the acceptance records.
  await expect(details.getByRole('link', { name: 'Terms and conditions' })).toHaveAttribute(
    'href',
    '/de/terms',
  );
  await expect(details.getByRole('link', { name: 'Privacy notice' })).toHaveAttribute(
    'href',
    '/de/privacy',
  );
  await page.getByTestId('accept-terms').check();
  await page.getByTestId('create-account').click();

  const sent = api.auth['complete-account-details']?.[0]?.details as Record<string, unknown>;
  expect(sent).toMatchObject({
    ticket: 'ticket-1',
    firstName: 'Ana',
    lastName: 'Kovač',
    isAdult: true,
    locale: 'DE',
    country: 'DE',
    acceptedTermsVersion: '2026-11',
  });
  expect(typeof sent.timeZone).toBe('string');

  // The account's language (German) takes over, and the phone step comes next.
  await expect(page).toHaveURL(/\/de\/verify-phone$/);
  await expect(page.getByTestId('phone-input')).toHaveValue('+49 ');

  // A +1 number gets the server's reason, translated, not a generic failure.
  await page.getByTestId('phone-input').fill('+1 415 555 0100');
  await page.getByTestId('send-sms').click();
  await expect(page.getByRole('main').getByRole('alert')).toHaveText(
    'An Nummern aus diesem Land können wir noch keine SMS senden. Bitte verwenden Sie eine andere Handynummer.',
  );

  await page.getByTestId('phone-input').fill('+49 151 23456789');
  await page.getByTestId('send-sms').click();
  await expect(page.getByTestId('phone-code')).toContainText('Gesendet an +49 ••• 789');
  await page.getByTestId('phone-code-input').fill('730912');
  await expect(page).toHaveURL(/\/de$/);
  await expect(page.getByTestId('signed-in-as')).toHaveText('Angemeldet als Ana');
  await expectNoTokensInStorage(page);
});

test('an existing account signs straight in and lands where it was going', async ({ page }) => {
  const api = await stubApi(page, {
    auth: {
      'verify-email-code': (_body, a) =>
        signedIn(a, 'verifyEmailCode', { locale: 'FR', phoneVerified: true }),
    },
  });
  await page.goto('/hr/sign-in?next=/de/bookings/12');
  await page.getByTestId('email').fill('ana.kovac@example.org');
  await page.getByTestId('send-code').click();
  await page.getByTestId('email-code-input').fill('482107');
  await expect(page).toHaveURL(/\/fr\/bookings\/12$/);
  expect(api.auth['verify-email-code']?.[0]).toMatchObject({ email: 'ana.kovac@example.org' });
  await expectNoTokensInStorage(page);
});

test('too many wrong codes show the lockout with its timer', async ({ page }) => {
  await stubApi(page, {
    auth: { 'verify-email-code': () => codeError('LOCKED', { retryAfterSeconds: 1800 }) },
  });
  await page.goto('/en/sign-in');
  await page.getByTestId('email').fill('ana.kovac@example.org');
  await page.getByTestId('send-code').click();
  await page.getByTestId('email-code-input').fill('482107');
  const locked = page.getByTestId('email-code-locked');
  await expect(locked).toContainText('Too many tries');
  await expect(locked).toContainText(/Try again in (30:00|29:5\d)\. Your account is safe\./);
});

test('an email that is not an address is caught before sending', async ({ page }) => {
  const api = await stubApi(page);
  await page.goto('/fr/sign-in');
  await page.getByTestId('email').fill('ana.kovac');
  await page.getByTestId('send-code').click();
  await expect(page.getByRole('main').getByRole('alert')).toHaveText(
    'Veuillez saisir une adresse e-mail valide.',
  );
  expect(api.operations.some((o) => o.name === 'RequestEmailCode')).toBe(false);
});

/** Google Identity Services, stubbed: its button hands back a fake ID token. */
async function stubGoogle(page: Page) {
  await page.route('https://accounts.google.com/gsi/client', (route) =>
    route.fulfill({
      contentType: 'text/javascript',
      body: `window.google = { accounts: { id: {
        initialize(options) { window.__gsi = options; },
        renderButton(parent) {
          const button = document.createElement('button');
          button.type = 'button';
          button.textContent = 'Google';
          button.dataset.testid = 'gsi-button';
          button.onclick = () => window.__gsi.callback({ credential: 'google-id-token' });
          parent.appendChild(button);
        },
      } } };`,
    }),
  );
}

test('Google sign-in for an existing email asks for the emailed code before linking', async ({
  page,
}) => {
  const api = await stubApi(page, {
    auth: {
      'sign-in-with-provider': () => ({
        data: {
          signInWithProvider: {
            __typename: 'ProviderLinkRequired',
            id: 'l1',
            email: 'ana.kovac@example.org',
            ticket: 'link-ticket',
          },
        },
      }),
      'link-provider': (_body, a) =>
        signedIn(a, 'linkProviderWithEmailCode', { locale: 'EN', phoneVerified: true }),
    },
  });
  await page.goto('/en/sign-in');
  await page.getByTestId('gsi-button').click();

  expect(api.auth['sign-in-with-provider']?.[0]).toMatchObject({
    provider: 'GOOGLE',
    idToken: 'google-id-token',
    nonce: 'nonce-1',
  });
  const link = page.getByTestId('link-code');
  await expect(link.getByRole('heading', { level: 1 })).toHaveText("Confirm it's you");
  await expect(link).toContainText(
    'ana.kovac@example.org already has a TaskBeavo account. To sign in to it with Google from now on, enter the 6-digit code we just emailed to that address.',
  );
  // Nothing is linked yet.
  expect(api.auth['link-provider']).toBeUndefined();

  await page.getByTestId('link-code-input').fill('482107');
  await expect(page).toHaveURL(/\/en$/);
  expect(api.auth['link-provider']?.[0]).toMatchObject({ ticket: 'link-ticket', code: '482107' });
  await expectNoTokensInStorage(page);
});

test('a new Google account finishes the account details first', async ({ page }) => {
  await stubApi(page, {
    auth: {
      'sign-in-with-provider': () => ({
        data: {
          signInWithProvider: {
            __typename: 'AccountDetailsRequired',
            id: 't1',
            email: 'ana@mail.example.com',
            ticket: 'ticket-2',
            firstName: 'Ana',
            lastName: 'Kovač',
          },
        },
      }),
    },
  });
  await page.goto('/hr/sign-in');
  await page.getByTestId('gsi-button').click();
  const details = page.getByTestId('account-details');
  await expect(details.getByRole('heading', { level: 1 })).toHaveText('Dovršite račun');
  await expect(details).toContainText('Prijavljeni putem Googlea');
  await expect(details).toContainText('ana@mail.example.com');
  await expect(page.getByTestId('first-name')).toHaveValue('Ana');
  await expect(page.getByTestId('create-account')).toBeDisabled();
});

test('terms changed while the form was open: the box is unticked for the new version', async ({
  page,
}) => {
  let version = '2026-11';
  const api = await stubApi(page, {
    auth: {
      'sign-in-with-provider': () => ({
        data: {
          signInWithProvider: {
            __typename: 'AccountDetailsRequired',
            id: 't1',
            email: 'ana@mail.example.com',
            ticket: 'ticket-2',
            firstName: 'Ana',
            lastName: 'Kovač',
          },
        },
      }),
      'complete-account-details': (body, a) => {
        const details = body.details as Record<string, unknown>;
        if (details.acceptedTermsVersion !== version) return codeError('TERMS_NOT_ACCEPTED');
        return signedIn(a, 'completeAccountDetails');
      },
    },
    graphql: {
      CurrentTerms: (variables) => ({
        data: {
          currentLegalDocument: {
            __typename: 'LegalDocument',
            id: `terms-${version}-${String(variables.language)}`,
            kind: 'TERMS',
            version,
            language: variables.language,
            effectiveAt: '2026-11-01T00:00:00Z',
          },
        },
      }),
    },
  });
  await page.goto('/en/sign-in');
  await page.getByTestId('gsi-button').click();
  await page.getByTestId('is-adult').check();
  await page.getByTestId('accept-terms').check();
  version = '2027-01';
  await page.getByTestId('create-account').click();

  await expect(page.getByTestId('account-details').getByRole('alert')).toHaveText(
    'The terms have been updated. Please read the current version and tick the box again.',
  );
  await expect(page.getByTestId('accept-terms')).not.toBeChecked();
  await page.getByTestId('accept-terms').check();
  await page.getByTestId('create-account').click();

  await expect.poll(() => api.auth['complete-account-details']?.length).toBe(2);
  const sent = api.auth['complete-account-details']!.map(
    (body) => (body.details as Record<string, unknown>).acceptedTermsVersion,
  );
  expect(sent).toEqual(['2026-11', '2027-01']);
});

test('no account can be created while the terms can not be loaded, until a retry loads them', async ({
  page,
}) => {
  let inForce: string | null = null;
  const api = await stubApi(page, {
    auth: {
      'sign-in-with-provider': () => ({
        data: {
          signInWithProvider: {
            __typename: 'AccountDetailsRequired',
            id: 't1',
            email: 'ana@mail.example.com',
            ticket: 'ticket-2',
            firstName: 'Ana',
            lastName: 'Kovač',
          },
        },
      }),
      'complete-account-details': (_body, a) => signedIn(a, 'completeAccountDetails'),
    },
    graphql: {
      CurrentTerms: (variables) => ({
        data: {
          currentLegalDocument: inForce && {
            __typename: 'LegalDocument',
            id: `terms-${String(variables.language)}`,
            kind: 'TERMS',
            version: inForce,
            language: variables.language,
            effectiveAt: '2026-11-01T00:00:00Z',
          },
        },
      }),
    },
  });
  await page.goto('/en/sign-in');
  await page.getByTestId('gsi-button').click();
  await page.getByTestId('is-adult').check();
  await page.getByTestId('accept-terms').check();

  await expect(page.getByTestId('account-details')).toContainText(
    "The terms can't be loaded right now",
  );
  await expect(page.getByTestId('create-account')).toBeDisabled();
  expect(api.auth['complete-account-details']).toBeUndefined();

  inForce = '2026-11';
  await page.getByTestId('terms-retry').click();
  await expect(page.getByTestId('terms-retry')).toBeHidden();
  await page.getByTestId('create-account').click();
  await expect
    .poll(() => api.auth['complete-account-details']?.[0]?.details)
    .toMatchObject({ acceptedTermsVersion: '2026-11' });
});

test('changing the account language unticks the terms box', async ({ page }) => {
  await stubApi(page, {
    auth: {
      'sign-in-with-provider': () => ({
        data: {
          signInWithProvider: {
            __typename: 'AccountDetailsRequired',
            id: 't1',
            email: 'ana@mail.example.com',
            ticket: 'ticket-2',
            firstName: 'Ana',
            lastName: 'Kovač',
          },
        },
      }),
    },
  });
  await page.goto('/en/sign-in');
  await page.getByTestId('gsi-button').click();
  const details = page.getByTestId('account-details');
  await page.getByTestId('is-adult').check();
  await page.getByTestId('accept-terms').check();
  await expect(page.getByTestId('create-account')).toBeEnabled();

  // Ticked while the links opened the English text; German needs its own tick.
  await details.getByText('DE', { exact: true }).click();
  await expect(page.getByTestId('accept-terms')).not.toBeChecked();
  await expect(page.getByTestId('create-account')).toBeDisabled();
  await expect(details.getByRole('link', { name: 'Terms and conditions' })).toHaveAttribute(
    'href',
    '/de/terms',
  );
});

async function signInByEmail(page: Page) {
  await page.getByTestId('email').fill('ana.kovac@example.org');
  await page.getByTestId('send-code').click();
  await page.getByTestId('email-code-input').fill('482107');
}

for (const answer of ['yes', 'no'] as const) {
  test(`a language picked while signed out is kept, and only "yes" saves it (${answer})`, async ({
    page,
  }) => {
    const api = await stubApi(page, {
      auth: {
        'verify-email-code': (_body, a) =>
          signedIn(a, 'verifyEmailCode', { locale: 'HR', phoneVerified: true }),
      },
    });
    await page.goto('/en/sign-in');
    await page
      .getByRole('navigation', { name: 'Language' })
      .getByRole('link', { name: 'Deutsch' })
      .click();
    await expect(page).toHaveURL(/\/de\/sign-in$/);
    await signInByEmail(page);

    const prompt = page.getByTestId('locale-prompt');
    await expect(prompt.getByRole('heading', { level: 1 })).toHaveText(
      'Deutsch auch für E-Mails und Benachrichtigungen verwenden?',
    );
    await expect(prompt).toContainText('Ihr Konto verwendet derzeit Hrvatski.');
    const before = api.operations.filter((o) => o.name === 'SetLocale').length;
    await page.getByTestId(answer === 'yes' ? 'locale-yes' : 'locale-no').click();
    await expect(page).toHaveURL(/\/de$/);
    const saves = api.operations.filter((o) => o.name === 'SetLocale').slice(before);
    expect(saves.map((o) => o.variables)).toEqual(answer === 'yes' ? [{ locale: 'DE' }] : []);
  });
}

test("without a pick, the account's language wins and nothing is asked", async ({ page }) => {
  await stubApi(page, {
    auth: {
      'verify-email-code': (_body, a) =>
        signedIn(a, 'verifyEmailCode', { locale: 'HR', phoneVerified: true }),
    },
  });
  await page.goto('/de/sign-in');
  await signInByEmail(page);
  await expect(page).toHaveURL(/\/hr$/);
  await expect(page.getByTestId('locale-prompt')).toHaveCount(0);
});

test('signing out clears the session and shows "Log in" again', async ({ page }) => {
  const api = await stubApi(page, {
    auth: { 'sign-out': (_body, a) => ((a.viewer = null), { data: { signOut: true } }) },
  });
  api.viewer = { ...user, phoneVerified: true };
  await page.goto('/en');
  await expect(page.getByTestId('signed-in-as')).toHaveText('Signed in as Ana');
  await page.getByTestId('sign-out').click();
  await expect(page.getByTestId('sign-in-link')).toBeVisible();
  expect(api.auth['sign-out']).toHaveLength(1);
});

test('"Log out of all devices" ends every session and shows "Log in" again', async ({ page }) => {
  const api = await stubApi(page, {
    auth: {
      'sign-out-everywhere': (_body, a) => (
        (a.viewer = null),
        { data: { signOutEverywhere: true } }
      ),
    },
  });
  api.viewer = { ...user, phoneVerified: true };
  await page.goto('/en');
  await page.getByTestId('sign-out-everywhere').click();
  await expect(page.getByTestId('sign-in-link')).toBeVisible();
  expect(api.auth['sign-out-everywhere']).toHaveLength(1);
});

test('"This wasn\'t me" logs out everywhere only after the button, not on opening', async ({
  page,
}) => {
  const api = await stubApi(page, {
    graphql: {
      SignOutEverywhereFromEmail: () => ({ data: { signOutEverywhereFromEmail: true } }),
    },
  });
  await page.goto('/de/this-wasnt-me?token=t0k3n');
  await expect(page.getByTestId('not-me')).toContainText('Waren Sie das nicht?');
  expect(api.operations.map((o) => o.name)).not.toContain('SignOutEverywhereFromEmail');

  await page.getByTestId('not-me-confirm').click();

  await expect(page.getByTestId('not-me-done')).toContainText('Sie sind überall abgemeldet');
  expect(api.operations.find((o) => o.name === 'SignOutEverywhereFromEmail')?.variables).toEqual({
    token: 't0k3n',
  });
});

test('an expired "This wasn\'t me" link says how to log out instead', async ({ page }) => {
  await stubApi(page, {
    graphql: { SignOutEverywhereFromEmail: () => codeError('SIGN_OUT_LINK_EXPIRED') },
  });
  await page.goto('/en/this-wasnt-me?token=old');
  await page.getByTestId('not-me-confirm').click();
  await expect(page.getByTestId('not-me-error')).toHaveText(
    'This link has expired. Log in and choose Log out of all devices instead.',
  );
});

test('the sign-in page fits a phone screen in German and French', async ({ page }) => {
  await stubApi(page);
  for (const locale of ['de', 'fr']) {
    await page.goto(`/${locale}/sign-in`);
    await expect(page.getByTestId('sign-in-start')).toBeVisible();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    );
    expect(overflow).toBe(false);
  }
});

test('an open sign-in page gets a new Google nonce before the old one expires', async ({
  page,
}) => {
  const start = new Date('2030-01-01T09:00:00Z');
  await page.clock.install({ time: start });
  // Nonces issued before the clock moves are "early", after it "late".
  let late = false;
  let issued = 0;
  const api = await stubApi(page, {
    graphql: {
      RequestSignInNonce: () => {
        issued += 1;
        const minutes = late ? 20 : 10; // the API's 10-minute TTL, on the page's clock
        return {
          data: {
            requestSignInNonce: {
              __typename: 'SignInNonce',
              id: `n${issued}`,
              nonce: `${late ? 'late' : 'early'}-${issued}`,
              expiresAt: new Date(start.getTime() + minutes * 60_000).toISOString(),
            },
          },
        };
      },
    },
    auth: {
      'sign-in-with-provider': (_body, a) =>
        signedIn(a, 'signInWithProvider', { locale: 'EN', phoneVerified: true }),
    },
  });
  const buttonNonce = () =>
    page.evaluate(() => (window as unknown as { __gsi?: { nonce?: string } }).__gsi?.nonce ?? '');
  await page.goto('/en/sign-in');
  await expect(page.getByTestId('gsi-button')).toBeVisible();
  await expect.poll(buttonNonce).toMatch(/^early-/);

  // Ten minutes later the button already carries a fresh nonce.
  late = true;
  await page.clock.fastForward('10:00');
  await expect.poll(buttonNonce).toMatch(/^late-/);
  const current = await buttonNonce();
  await page.getByTestId('gsi-button').click();

  await expect(page).toHaveURL(/\/en$/);
  expect(api.auth['sign-in-with-provider']?.[0]).toMatchObject({ nonce: current });
});

for (const [clock, expiresAt] of [
  ['fast (expiresAt already past)', '2030-01-01T08:40:00Z'],
  ['slow', '2030-01-01T09:40:00Z'],
  ['unknown (no expiresAt)', null],
] as const) {
  test(`a device clock that is ${clock} does not re-issue Google nonces in a loop`, async ({
    page,
  }) => {
    await page.clock.install({ time: new Date('2030-01-01T09:00:00Z') });
    let issued = 0;
    await stubApi(page, {
      graphql: {
        RequestSignInNonce: () => {
          issued += 1;
          return {
            data: {
              requestSignInNonce: {
                __typename: 'SignInNonce',
                id: `n${issued}`,
                nonce: `nonce-${issued}`,
                expiresAt,
              },
            },
          };
        },
      },
    });
    await page.goto('/en/sign-in');
    await expect(page.getByTestId('gsi-button')).toBeVisible();
    await page.clock.runFor(2_000);
    const atLoad = issued;

    await page.clock.runFor('08:30');
    expect(issued).toBe(atLoad);
    await page.clock.runFor('01:00');
    await expect.poll(() => issued).toBeGreaterThan(atLoad);
  });
}

test('phone verification shows an error with retry when the account cannot be loaded', async ({
  page,
}) => {
  const api = await stubApi(page);
  api.viewer = { ...user };
  // Viewer requests fail as if offline until the connection comes back.
  let offline = true;
  await page.route('**/api/graphql', async (route) => {
    const body = route.request().postDataJSON() as { operationName: string };
    if (body.operationName === 'Viewer' && offline) {
      await route.abort('internetdisconnected');
    } else {
      await route.fallback();
    }
  });
  await page.goto('/de/verify-phone');

  const failure = page.getByTestId('phone-load-error');
  await expect(failure.getByRole('alert')).toHaveText(
    'TaskBeavo ist gerade nicht erreichbar. Prüfen Sie Ihre Verbindung und versuchen Sie es erneut.',
  );
  offline = false;
  await failure.getByTestId('phone-retry').click();
  await expect(page.getByTestId('phone-input')).toHaveValue('+49 ');
});

test('a double tap on "send a new code" sends one SMS', async ({ page }) => {
  const api = await stubApi(page, {
    graphql: {
      RequestPhoneCode: () => ({
        data: {
          requestPhoneCode: {
            __typename: 'PhoneCodeSent',
            id: 'p1',
            phoneNumberMasked: '+49 ••• 789',
            expiresAt: '2030-01-01T00:00:00Z',
            resendAvailableAt: '2000-01-01T00:00:00Z',
          },
        },
      }),
    },
  });
  api.viewer = { ...user };
  await page.goto('/de/verify-phone');
  await page.getByTestId('phone-input').fill('+49 151 23456789');
  await page.getByTestId('send-sms').click();
  await expect(page.getByTestId('phone-code')).toBeVisible();

  // The new code takes a moment to go out.
  await page.route('**/api/graphql', async (route) => {
    const body = route.request().postDataJSON() as { operationName: string };
    if (body.operationName === 'RequestPhoneCode') await new Promise((r) => setTimeout(r, 500));
    await route.fallback();
  });
  const resend = page.getByTestId('phone-code').getByRole('button', { name: 'Neuen Code senden' });
  await resend.click();
  await resend.click({ force: true });
  await expect(page.getByTestId('phone-code')).toContainText('Wir haben Ihnen einen neuen Code');

  expect(api.operations.filter((o) => o.name === 'RequestPhoneCode')).toHaveLength(2);
});

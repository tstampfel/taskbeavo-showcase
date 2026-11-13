import type { Page } from '@playwright/test';
import { expect, test, type Answer, type Answers, type Variables } from './graphql-stub';

/**
 * Settings → Business details (BEAV-199) against a stubbed API, at the mobile (375px) and
 * desktop widths of playwright.config.ts. Which OIBs are valid, and the withdrawal of private
 * helpers' offers, is the server's job (backend accounts/tests); here the page explains it,
 * asks first, sends what was typed and shows what comes back.
 */

// English strings from packages/i18n (Playwright can't import the JSON catalogues).
const en = {
  title: 'Business details',
  privatePoster: 'You book as a private person.',
  confirmTitle: 'Book as a business?',
  confirmBody:
    "As a business you can only book registered businesses (obrt or company). Open offers from private helpers on your tasks will be withdrawn, and private helpers can't send new ones.",
  saved: 'Business details saved.',
  removed: 'Business details removed.',
  removeTitle: 'Remove business details?',
  removeBody: "You'll book as a private person again. Offers withdrawn earlier stay withdrawn.",
  oibReenter: "For your security we don't show the saved OIB. Enter it again to save changes.",
  invalidOib:
    "This OIB isn't valid here. Check the 11 digits. For a company, enter the company's OIB, not a person's.",
  invalidObrtOib: "An obrt uses its owner's own OIB. Enter the same OIB as on your helper profile.",
  company: 'Company',
  POSTER_BUSINESS_CHECKOUT_OPEN:
    'You have an unfinished checkout with a private helper. Finish or leave it first, then add your business.',
  INVALID_POSTER_BUSINESS:
    'Please choose obrt or company and enter the business name (up to 200 characters).',
  UNAUTHENTICATED: 'Please sign in to continue.',
  RATE_LIMITED: 'Too many attempts. Please wait a while and try again.',
};
const confirmTitles = { de: 'Als Unternehmen buchen?', fr: 'Réserver en tant qu’entreprise ?' };
// Made-up digits, not a real OIB: the API is stubbed, so the check digit doesn't matter.
const OIB = '12345678901';
const MASKED = '•••••••8901';

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

type Business = { kind: string; name: string; oibMasked: string } | null;
const user = (business: Business) => ({
  __typename: 'User',
  id: viewer.id,
  posterBusiness: business && { __typename: 'PosterBusiness', id: 'pb-1', ...business },
});
const failure = (code: string): Answer => ({
  data: null,
  errors: [{ message: 'refused', extensions: { code } }],
});

function answers(
  state: { business: Business },
  extra: Partial<Record<string, (variables: Variables) => Answer>> = {},
): Answers {
  return {
    Viewer: () => ({ data: { viewer } }),
    TermsStatus: () => ({
      data: {
        viewer: { __typename: 'User', id: viewer.id, mustAcceptTerms: false, termsToAccept: null },
      },
    }),
    ApiVersion: () => ({ data: { apiVersion: 'test' } }),
    // Settings → Privacy and data (BEAV-83) sits under Business details.
    Consents: () => ({ data: { consents: [] } }),
    PosterBusiness: () => ({ data: { viewer: user(state.business) } }),
    SetPosterBusiness: ({ input }) => {
      const { kind, name } = input as { kind: string; name: string };
      state.business = { kind, name, oibMasked: MASKED };
      return { data: { setPosterBusiness: user(state.business) } };
    },
    ClearPosterBusiness: () => {
      state.business = null;
      return { data: { clearPosterBusiness: user(null) } };
    },
    ...(extra as Answers),
  };
}

async function fillForm(page: Page, { kind = 'OBRT', name = 'Obrt Primjer' } = {}) {
  await page.getByTestId(`business-kind-${kind}`).check({ force: true });
  await page.getByTestId('business-name').fill(name);
  await page.getByTestId('business-oib-input').fill(OIB);
  await page.getByTestId('business-save').click();
}

const noHorizontalScroll = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);

test('adds a business after explaining what changes, then shows only the masked OIB', async ({
  page,
  stubGraphql,
}) => {
  const stub = await stubGraphql(answers({ business: null }));
  await page.goto('/en/settings');

  await expect(page.getByRole('heading', { level: 1 })).toHaveText(en.title);
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute('content', 'noindex, nofollow');
  await expect(page.getByTestId('business-none')).toHaveText(en.privatePoster);
  await page.getByTestId('business-add').click();
  await fillForm(page, { name: '  Obrt Primjer ' });

  const dialog = page.getByRole('dialog', { name: en.confirmTitle });
  await expect(dialog).toContainText(en.confirmBody);
  await dialog.getByTestId('business-confirm').click();

  await expect(page.getByTestId('business-notice')).toHaveText(en.saved);
  await expect(page.getByTestId('business-oib')).toHaveText(`OIB ${MASKED}`);
  await expect(page.getByText(OIB)).toHaveCount(0);
  expect(stub.calls.find((call) => call.name === 'SetPosterBusiness')?.variables).toEqual({
    input: { kind: 'OBRT', name: 'Obrt Primjer', oib: OIB },
  });
});

test('cancelling the confirmation saves nothing', async ({ page, stubGraphql }) => {
  const stub = await stubGraphql(answers({ business: null }));
  await page.goto('/en/settings');
  await page.getByTestId('business-add').click();
  await fillForm(page);

  const dialog = page.getByRole('dialog', { name: en.confirmTitle });
  await dialog.getByTestId('business-confirm-cancel').click();
  await expect(dialog).toBeHidden();
  await expect(page.getByTestId('business-form')).toBeVisible();
  expect(stub.calls.map((call) => call.name)).not.toContain('SetPosterBusiness');
});

test('edits the saved business: the OIB is asked for again, never shown', async ({
  page,
  stubGraphql,
}) => {
  const stub = await stubGraphql(
    answers({ business: { kind: 'OBRT', name: 'Obrt Primjer', oibMasked: MASKED } }),
  );
  await page.goto('/en/settings');

  await expect(page.getByTestId('business-oib')).toHaveText(`OIB ${MASKED}`);
  await page.getByTestId('business-edit').click();
  await expect(page.getByTestId('business-name')).toHaveValue('Obrt Primjer');
  await expect(page.getByTestId('business-oib-input')).toHaveValue('');
  await expect(page.getByText(en.oibReenter)).toBeVisible();

  await fillForm(page, { kind: 'COMPANY', name: 'Primjer d.o.o.' });
  await page.getByRole('dialog', { name: en.confirmTitle }).getByTestId('business-confirm').click();

  await expect(page.getByTestId('business-notice')).toHaveText(en.saved);
  await expect(page.getByTestId('business-saved')).toContainText('Primjer d.o.o.');
  await expect(page.getByTestId('business-saved')).toContainText(en.company);
  expect(stub.calls.find((call) => call.name === 'SetPosterBusiness')?.variables).toEqual({
    input: { kind: 'COMPANY', name: 'Primjer d.o.o.', oib: OIB },
  });
});

test('removes the business after confirmation', async ({ page, stubGraphql }) => {
  await stubGraphql(
    answers({ business: { kind: 'OBRT', name: 'Obrt Primjer', oibMasked: MASKED } }),
  );
  await page.goto('/en/settings');

  await page.getByTestId('business-remove').click();
  const dialog = page.getByRole('dialog', { name: en.removeTitle });
  await expect(dialog).toContainText(en.removeBody);
  await dialog.getByTestId('business-remove-confirm').click();

  await expect(page.getByTestId('business-notice')).toHaveText(en.removed);
  await expect(page.getByTestId('business-none')).toHaveText(en.privatePoster);
});

test('says so and keeps the business when removing fails', async ({ page, stubGraphql }) => {
  await stubGraphql(
    answers(
      { business: { kind: 'OBRT', name: 'Obrt Primjer', oibMasked: MASKED } },
      { ClearPosterBusiness: () => failure('UNAUTHENTICATED') },
    ),
  );
  await page.goto('/en/settings');

  await page.getByTestId('business-remove').click();
  await page
    .getByRole('dialog', { name: en.removeTitle })
    .getByTestId('business-remove-confirm')
    .click();

  await expect(page.getByTestId('business-details').getByRole('alert')).toHaveText(
    en.UNAUTHENTICATED,
  );
  await expect(page.getByTestId('business-oib')).toHaveText(`OIB ${MASKED}`);
  await expect(page.getByTestId('business-notice')).toHaveCount(0);
});

test('clears the OIB error when the kind changes', async ({ page, stubGraphql }) => {
  await stubGraphql(
    answers({ business: null }, { SetPosterBusiness: () => failure('INVALID_BUSINESS_OIB') }),
  );
  await page.goto('/en/settings');
  await page.getByTestId('business-add').click();
  await fillForm(page);
  await page.getByRole('dialog', { name: en.confirmTitle }).getByTestId('business-confirm').click();
  await expect(page.getByText(en.invalidObrtOib)).toBeVisible();

  await page.getByTestId('business-kind-COMPANY').check({ force: true });
  await expect(page.getByText(en.invalidObrtOib)).toHaveCount(0);
});

for (const [code, message] of [
  ['INVALID_OIB', en.invalidOib],
  ['INVALID_BUSINESS_OIB', en.invalidObrtOib],
  ['POSTER_BUSINESS_CHECKOUT_OPEN', en.POSTER_BUSINESS_CHECKOUT_OPEN],
  ['INVALID_POSTER_BUSINESS', en.INVALID_POSTER_BUSINESS],
  ['RATE_LIMITED', en.RATE_LIMITED],
] as const) {
  test(`shows ${code} as a friendly message and keeps the form`, async ({ page, stubGraphql }) => {
    await stubGraphql(answers({ business: null }, { SetPosterBusiness: () => failure(code) }));
    await page.goto('/en/settings');
    await page.getByTestId('business-add').click();
    await fillForm(page, { kind: 'COMPANY' });
    await page
      .getByRole('dialog', { name: en.confirmTitle })
      .getByTestId('business-confirm')
      .click();

    await expect(page.getByTestId('business-form').getByRole('alert')).toHaveText(message);
    await expect(page.getByTestId('business-oib-input')).toHaveValue(OIB);
  });
}

test('the account menu links to Settings', async ({ page, stubGraphql }) => {
  await stubGraphql(answers({ business: null }));
  await page.goto('/en');
  await page.getByTestId('settings-link').click();
  await expect(page).toHaveURL(/\/en\/settings$/);
  await expect(page.getByTestId('business-none')).toBeVisible();
});

test('the form and its confirmation fit at every width in German and French', async ({
  page,
  stubGraphql,
}) => {
  await stubGraphql(answers({ business: null }));
  for (const locale of ['de', 'fr'] as const) {
    await page.goto(`/${locale}/settings`);
    await page.getByTestId('business-add').click();
    await fillForm(page);
    await expect(page.getByRole('dialog', { name: confirmTitles[locale] })).toBeVisible();
    expect(await noHorizontalScroll(page)).toBe(true);
  }
});

test('sends a signed-out visitor to sign-in and back', async ({ page, stubGraphql }) => {
  await stubGraphql({
    ...answers({ business: null }),
    Viewer: () => ({ data: { viewer: null } }),
    PosterBusiness: () => ({ data: { viewer: null } }),
    TermsStatus: () => ({ data: { viewer: null } }),
    // The sign-in page asks for a nonce as it opens.
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
  });
  await page.goto('/en/settings');
  await expect(page).toHaveURL(/\/en\/sign-in\?next=%2Fsettings$/);
});

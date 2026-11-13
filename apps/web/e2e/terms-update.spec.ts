import { expect, test, type Page, type Route } from '@playwright/test';

/**
 * "We've updated our terms" and limited mode (mockups 21b and 21c) against a stubbed API, at
 * the mobile (375px) and desktop widths of playwright.config.ts: the first page of a visit
 * opens the screen, Accept needs the unticked box, and acceptCurrentTerms sends the version and
 * language shown. "Not now" leaves the account limited, with the banner on every page.
 */

const viewer = {
  __typename: 'User',
  id: 'u1',
  email: 'ana@example.com',
  firstName: 'Ana',
  lastName: 'Kovač',
  locale: 'DE',
  country: 'DE',
  timeZone: 'Europe/Berlin',
  phoneVerified: true,
  phoneNumberMasked: '+49 ••• 678',
};

interface Stub {
  mustAccept: boolean;
  version: string;
  kind: 'TERMS' | 'HELPER_TERMS';
  /** termsToAccept answers null while the account must accept, as when it can't be read. */
  termsMissing: boolean;
  calls: { name: string; variables: Record<string, unknown> }[];
  refuseNextAccept: boolean;
  /** How many TermsStatus calls still fail, as a server error would. */
  failingStatus: number;
}

async function stubApi(page: Page): Promise<Stub> {
  const stub: Stub = {
    mustAccept: true,
    version: '2026-12',
    kind: 'TERMS',
    termsMissing: false,
    calls: [],
    refuseNextAccept: false,
    failingStatus: 0,
  };
  await page.route('**/api/graphql', async (route: Route) => {
    const { operationName, variables = {} } = route.request().postDataJSON() as {
      operationName: string;
      variables?: Record<string, unknown> & { language?: string };
    };
    stub.calls.push({ name: operationName, variables });
    if (operationName === 'TermsStatus' && stub.failingStatus > 0) {
      stub.failingStatus -= 1;
      await route.fulfill({ status: 503, json: { errors: [{ message: 'Unavailable' }] } });
      return;
    }
    const status = () => ({
      __typename: 'User',
      id: 'u1',
      mustAcceptTerms: stub.mustAccept,
      termsToAccept: stub.mustAccept
        ? { __typename: 'LegalDocument', id: `terms-${stub.version}`, version: stub.version }
        : null,
    });
    let answer: unknown;
    if (operationName === 'AcceptCurrentTerms' && stub.refuseNextAccept) {
      stub.refuseNextAccept = false;
      stub.version = '2027-01';
      answer = {
        data: null,
        errors: [
          {
            message: 'Accept the terms version in force.',
            extensions: { code: 'TERMS_NOT_ACCEPTED' },
          },
        ],
      };
    } else if (operationName === 'AcceptCurrentTerms') {
      stub.mustAccept = false;
      answer = {
        data: {
          acceptCurrentTerms: { ...viewer, ...status(), accountState: 'ACTIVE' },
        },
      };
    } else {
      answer = {
        Viewer: { data: { viewer } },
        TermsStatus: { data: { viewer: status() } },
        TermsToAccept: {
          data: {
            viewer: {
              __typename: 'User',
              id: 'u1',
              termsToAccept:
                stub.mustAccept && !stub.termsMissing
                  ? {
                      __typename: 'LegalDocument',
                      id: `terms-${stub.version}`,
                      kind: stub.kind,
                      version: stub.version,
                      language: variables.language ?? 'EN',
                      effectiveAt: '2026-12-01T00:00:00Z',
                    }
                  : null,
            },
          },
        },
        ApiVersion: { data: { apiVersion: 'test' } },
      }[operationName];
    }
    await route.fulfill({ json: answer ?? { data: null } });
  });
  return stub;
}

const noHorizontalScroll = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);

test('the first page of a visit opens the screen, and accepting ends limited mode', async ({
  page,
}) => {
  const stub = await stubApi(page);
  await page.goto('/de');

  await expect(page).toHaveURL(/\/de\/terms-update\?next=%2F$/);
  await expect(
    page
      .getByTestId('app-top-bar')
      .getByRole('heading', { level: 1, name: 'Wir haben unsere AGB aktualisiert' }),
  ).toBeVisible();
  await expect(page.getByRole('heading', { level: 1 })).toHaveCount(1);
  await expect(page.getByTestId('terms-update-version')).toContainText('2026-12');
  await expect(page.getByTestId('terms-update-version')).toContainText('1. Dezember 2026');
  const box = page.getByTestId('terms-update-agree');
  const accept = page.getByTestId('terms-update-accept');
  await expect(box).not.toBeChecked();
  await expect(accept).toBeDisabled();
  expect(await noHorizontalScroll(page)).toBe(true);

  await box.check();
  await accept.click();

  await expect(page).toHaveURL(/\/de$/);
  await expect(page.getByTestId('terms-banner')).toHaveCount(0);
  expect(stub.calls.find((call) => call.name === 'AcceptCurrentTerms')?.variables).toEqual({
    version: '2026-12',
    language: 'DE',
  });
});

test('"Not now" stays limited, with the banner leading back', async ({ page }) => {
  const stub = await stubApi(page);
  await page.goto('/en');
  await expect(page).toHaveURL(/\/en\/terms-update/);

  await page.getByTestId('terms-update-not-now').click();

  await expect(page).toHaveURL(/\/en$/);
  const banner = page.getByTestId('terms-banner');
  await expect(banner).toContainText('Accept the updated terms to post or book');
  expect(await noHorizontalScroll(page)).toBe(true);
  // Once per visit: another page doesn't open the screen again.
  await page.goto('/en/verify-phone');
  await expect(page).toHaveURL(/\/en\/verify-phone$/);
  await expect(page.getByTestId('terms-banner')).toBeVisible();

  await page.getByRole('link', { name: 'Review and accept' }).click();
  await expect(page).toHaveURL(/\/en\/terms-update\?next=%2Fverify-phone$/);
  expect(stub.calls.some((call) => call.name === 'AcceptCurrentTerms')).toBe(false);
});

test('terms that changed meanwhile untick the box and show the new version', async ({ page }) => {
  const stub = await stubApi(page);
  stub.refuseNextAccept = true;
  await page.goto('/fr/terms-update');

  await page.getByTestId('terms-update-agree').check();
  await page.getByTestId('terms-update-accept').click();

  await expect(page.getByTestId('terms-update').getByRole('alert')).toBeVisible();
  await expect(page.getByTestId('terms-update-agree')).not.toBeChecked();
  await expect(page.getByTestId('terms-update-version')).toContainText('2027-01');
  expect(stub.mustAccept).toBe(true);
});

test('landing on the page directly, "Not now" stays where it leads', async ({ page }) => {
  await stubApi(page);
  await page.goto('/en/terms-update');
  await expect(page.getByTestId('terms-update')).toBeVisible();

  await page.getByTestId('terms-update-not-now').click();

  await expect(page).toHaveURL(/\/en$/);
  await expect(page.getByTestId('terms-banner')).toBeVisible();
  // The banner would open the page again right after mounting; give it the chance.
  await page.waitForTimeout(1_000);
  await expect(page).toHaveURL(/\/en$/);
  await expect(page.getByTestId('terms-banner')).toBeVisible();
});

test('a failed status check says so, with Try again and Not now', async ({ page }) => {
  const stub = await stubApi(page);
  stub.failingStatus = 1;
  await page.goto('/de/terms-update');

  const failed = page.getByTestId('terms-update-load-error');
  await expect(failed).toBeVisible();
  await expect(failed.getByRole('alert')).toHaveText(
    'Die AGB können gerade nicht geladen werden. Versuchen Sie es gleich noch einmal.',
  );
  expect(await noHorizontalScroll(page)).toBe(true);

  await page.getByTestId('terms-update-status-retry').click();

  await expect(page.getByTestId('terms-update-agree')).not.toBeChecked();
});

test('"Not now" works from the failed status check too', async ({ page }) => {
  const stub = await stubApi(page);
  stub.failingStatus = 1;
  await page.goto('/en/terms-update?next=%2Fverify-phone');

  await page.getByTestId('terms-update-not-now').click();

  await expect(page).toHaveURL(/\/en\/verify-phone$/);
});

test('another account signing in on the tab gets the screen again', async ({ page }) => {
  await stubApi(page);
  await page.goto('/en');
  await expect(page).toHaveURL(/\/en\/terms-update/);
  await page.getByTestId('terms-update-not-now').click();
  await expect(page.getByTestId('terms-banner')).toBeVisible();

  // Another account was last shown the screen in this tab.
  await page.evaluate(() => sessionStorage.setItem('taskbeavo.termsUpdateShownTo', 'u2:2026-12'));
  await page.goto('/en');

  await expect(page).toHaveURL(/\/en\/terms-update/);
});

test('the tick box names the version and date that accepting records', async ({ page }) => {
  const stub = await stubApi(page);
  await page.goto('/de/terms-update');

  const box = page.getByTestId('terms-update-agree');
  const line = page.locator('label').filter({ has: box });
  await expect(line).toHaveText(
    'Ich habe die aktualisierten AGB (Version 2026-12, gültig ab 1. Dezember 2026) gelesen und stimme ihnen zu.',
  );
  await expect(page.getByTestId('terms-update-bookings')).toHaveText(
    'Für Buchungen, die vor dem 1. Dezember 2026 angenommen wurden, gelten weiterhin die Bedingungen, die bei ihrer Annahme galten.',
  );
  await box.check();
  await page.getByTestId('terms-update-accept').click();

  await expect(page).toHaveURL(/\/de$/);
  expect(stub.calls.find((call) => call.name === 'AcceptCurrentTerms')?.variables).toEqual({
    version: '2026-12',
    language: 'DE',
  });
});

for (const [locale, text] of [
  ['hr', 'Ako se ne slažete'],
  ['en', "If you don't agree"],
  ['de', 'Wenn Sie nicht zustimmen'],
  ['fr', 'Si vous n’êtes pas d’accord'],
] as const) {
  test(`the refusal line shows in ${locale}, linking the Privacy notice`, async ({ page }) => {
    await stubApi(page);
    await page.goto(`/${locale}/terms-update`);

    await expect(page.getByTestId('terms-update-refuse')).toContainText(text);
    // No account deletion page yet: the Privacy notice says how to close the account.
    await expect(page.getByTestId('terms-update-close')).toHaveAttribute(
      'href',
      `/${locale}/privacy`,
    );
    expect(await noHorizontalScroll(page)).toBe(true);
  });
}

test('a helper re-accepting HELPER_TERMS sees the Helper terms, not the Terms', async ({
  page,
}) => {
  const stub = await stubApi(page);
  stub.kind = 'HELPER_TERMS';
  await page.goto('/en/terms-update');

  await expect(page.getByTestId('terms-update-version')).toContainText('Helper terms');
  await expect(page.getByTestId('terms-update-read')).toHaveText('Read the full Helper terms');
  await expect(page.getByTestId('terms-update-read')).toHaveAttribute('href', '/en/helper-terms');
  const line = page.locator('label').filter({ has: page.getByTestId('terms-update-agree') });
  await expect(line.getByRole('link', { name: 'Helper terms' })).toHaveAttribute(
    'href',
    '/en/helper-terms',
  );
  await expect(line).not.toContainText('Terms and conditions');
});

test('no tick box and no half-filled version while the terms are unavailable', async ({ page }) => {
  const stub = await stubApi(page);
  stub.termsMissing = true;
  await page.goto('/en/terms-update');

  await expect(page.getByTestId('terms-update-retry')).toBeVisible();
  await expect(page.getByTestId('terms-update-agree')).toHaveCount(0);
  await expect(page.getByTestId('terms-update-version')).toHaveCount(0);
  await expect(page.getByTestId('terms-update')).not.toContainText('version ,');
  await expect(page.getByTestId('terms-update-accept')).toBeDisabled();
});

test('a newer version taking effect in the same visit opens the screen again', async ({ page }) => {
  const stub = await stubApi(page);
  await page.goto('/en');
  await expect(page).toHaveURL(/\/en\/terms-update/);
  await page.getByTestId('terms-update-not-now').click();
  await expect(page.getByTestId('terms-banner')).toBeVisible();

  stub.version = '2027-01';
  await page.goto('/en/verify-phone');

  await expect(page).toHaveURL(/\/en\/terms-update\?next=%2Fverify-phone$/);
  await expect(page.getByTestId('terms-update-version')).toContainText('2027-01');
});

test('leaving a page before the screen opens still opens it on the next page', async ({ page }) => {
  const stub = await stubApi(page);
  await page.goto('/en');
  await expect(page).toHaveURL(/\/en\/terms-update/);
  await page.getByTestId('terms-update-not-now').click();
  await expect(page.getByTestId('terms-banner')).toBeVisible();

  // A newer version reaches the next page's banner, but its way to the screen never lands.
  stub.version = '2027-01';
  const held = (url: URL) => url.pathname === '/en/terms-update' && url.searchParams.has('_rsc');
  await page.route(held, () => undefined);
  const opening = page.waitForRequest((request) => held(new URL(request.url())));
  await page.getByTestId('legal-footer').getByRole('link', { name: 'Fees' }).click();
  await opening;
  await page.unroute(held);

  await page.goto('/en/verify-phone');
  await expect(page).toHaveURL(/\/en\/terms-update\?next=%2Fverify-phone$/);
  await expect(page.getByTestId('terms-update-version')).toContainText('2027-01');
});

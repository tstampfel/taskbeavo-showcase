import type { Page, Route } from '@playwright/test';
import { expect, test, type Answers } from './graphql-stub';

/**
 * The app shell of logged-in pages (BEAV-209): a top bar with back, the page title and the
 * account menu, which holds the session controls and the language switcher. Runs at the
 * mobile (375px) and desktop (1280px) widths of playwright.config.ts, on the payouts pages,
 * which load nothing but the viewer.
 */

const viewer = {
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

function answers(state: { signedIn: boolean }): Answers {
  return {
    Viewer: () => ({ data: { viewer: state.signedIn ? viewer : null } }),
    TermsStatus: () => ({
      data: {
        viewer: { __typename: 'User', id: viewer.id, mustAcceptTerms: false, termsToAccept: null },
      },
    }),
    // The home page's API status line, after signing out.
    ApiVersion: () => ({ data: { apiVersion: 'test' } }),
    // Settings, opened from the menu.
    PosterBusiness: () => ({
      data: { viewer: { __typename: 'User', id: viewer.id, posterBusiness: null } },
    }),
    Consents: () => ({ data: { consents: [] } }),
    SetLocale: ({ locale }) => ({
      data: { setLocale: { __typename: 'User', id: viewer.id, locale } },
    }),
  };
}

/** Answers /api/auth/<name> and records each call. */
async function stubAuth(
  page: Page,
  handlers: Record<string, () => { data?: unknown; errors?: unknown[] }>,
) {
  const calls: string[] = [];
  await page.route('**/api/auth/*', async (route: Route) => {
    const name = new URL(route.request().url()).pathname.split('/').pop()!;
    calls.push(name);
    await route.fulfill({ json: handlers[name]?.() ?? { data: null } });
  });
  return calls;
}

async function noHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
}

test('a logged-in page starts with the top bar, not the session strip', async ({
  page,
  stubGraphql,
}) => {
  await stubGraphql(answers({ signedIn: true }));
  await page.goto('/en/helper/payouts/return');

  const bar = page.getByTestId('app-top-bar');
  await expect(bar.getByRole('heading', { level: 1 })).toHaveText('Payouts');
  await expect(page.getByRole('heading', { level: 1 })).toHaveCount(1);
  // The account controls wait behind the menu button.
  await expect(page.getByTestId('account-menu-button')).toBeVisible();
  await expect(page.getByTestId('signed-in-as')).toBeHidden();
  await expect(page.getByTestId('sign-out')).toBeHidden();
  await expect(page.getByTestId('language-switcher')).toBeHidden();
  await expect(page.getByTestId('site-header')).toHaveCount(0);

  // 44px tap targets, and the bar sits above the page.
  for (const id of ['app-back', 'account-menu-button']) {
    const box = await page.getByTestId(id).boundingBox();
    expect(box?.width).toBeGreaterThanOrEqual(44);
    expect(box?.height).toBeGreaterThanOrEqual(44);
  }
  const barBox = await bar.boundingBox();
  const mainBox = await page.getByRole('main').boundingBox();
  expect(barBox!.y + barBox!.height).toBeLessThanOrEqual(mainBox!.y);
  await noHorizontalScroll(page);

  // Back opens the parent page; a page without a parent has no back button.
  await bar.getByRole('link', { name: 'Back', exact: true }).click();
  await expect(page).toHaveURL(/\/en\/helper\/payouts$/);
  await expect(page.getByTestId('app-title')).toHaveText('Payouts');
  await expect(page.getByTestId('app-back')).toHaveCount(0);
});

test('the account menu opens with one tap and closes with Escape or a tap outside', async ({
  page,
  stubGraphql,
}) => {
  await stubGraphql(answers({ signedIn: true }));
  await page.goto('/en/helper/payouts');
  const button = page.getByRole('button', { name: 'Account and language' });
  const menu = page.getByTestId('account-menu');

  await button.click();
  await expect(button).toHaveAttribute('aria-expanded', 'true');
  await expect(menu.getByTestId('signed-in-as')).toHaveText('Signed in as Ana');
  await expect(menu.getByTestId('settings-link')).toBeVisible();
  await expect(menu.getByRole('navigation', { name: 'Language' })).toBeVisible();
  await expect(menu.getByTestId('sign-out')).toBeVisible();
  await expect(menu.getByTestId('sign-out-everywhere')).toBeVisible();

  await page.keyboard.press('Escape');
  await expect(menu).toBeHidden();
  await expect(button).toBeFocused();
  await expect(button).toHaveAttribute('aria-expanded', 'false');

  await button.click();
  await expect(menu).toBeVisible();
  await page.getByRole('main').click({ position: { x: 5, y: 5 } });
  await expect(menu).toBeHidden();

  await button.click();
  await menu.getByTestId('settings-link').click();
  await expect(page).toHaveURL(/\/en\/settings$/);
  await expect(menu).toBeHidden();
});

test('tabbing past the last item closes the menu; a tap on its text keeps it open', async ({
  page,
  stubGraphql,
}) => {
  await stubGraphql(answers({ signedIn: true }));
  await page.goto('/en/helper/payouts/return');
  const button = page.getByTestId('account-menu-button');
  const menu = page.getByTestId('account-menu');

  await button.click();
  await menu.getByTestId('signed-in-as').click();
  await expect(menu).toBeVisible();

  // Settings, the four languages, Sign out and Log out of all devices, then out of the menu.
  await button.focus();
  await page.keyboard.press('Tab');
  await expect(menu.getByTestId('settings-link')).toBeFocused();
  for (let i = 0; i < 6; i++) await page.keyboard.press('Tab');
  await expect(menu.getByTestId('sign-out-everywhere')).toBeFocused();
  await expect(menu).toBeVisible();
  await page.keyboard.press('Tab');
  await expect(menu).toBeHidden();
  await expect(button).toHaveAttribute('aria-expanded', 'false');

  // Shift+Tab back from the button to the back link closes it too.
  await button.click();
  await expect(menu).toBeVisible();
  await button.focus();
  await page.keyboard.press('Shift+Tab');
  await expect(menu).toBeHidden();
});

for (const locale of ['de', 'fr'] as const) {
  test(`the open menu fits a 375px screen in ${locale}`, async ({ page, stubGraphql }) => {
    await stubGraphql(answers({ signedIn: true }));
    await page.goto(`/${locale}/helper/payouts/return`);
    await page.getByTestId('account-menu-button').click();
    const menu = page.getByTestId('account-menu');
    await expect(menu.getByTestId('sign-out-everywhere')).toBeVisible();

    const box = (await menu.boundingBox())!;
    const width = page.viewportSize()!.width;
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(width);
    for (const id of ['sign-out', 'sign-out-everywhere', 'settings-link']) {
      const control = (await menu.getByTestId(id).boundingBox())!;
      expect(control.height).toBeGreaterThanOrEqual(44);
      expect(control.x + control.width).toBeLessThanOrEqual(box.x + box.width);
    }
    await noHorizontalScroll(page);
  });
}

test('the language switcher in the menu opens the same page in another language', async ({
  page,
  stubGraphql,
}) => {
  const stub = await stubGraphql(answers({ signedIn: true }));
  await page.goto('/en/helper/payouts/return');
  await page.getByTestId('account-menu-button').click();
  await page
    .getByTestId('account-menu')
    .getByRole('navigation', { name: 'Language' })
    .getByRole('link', { name: 'Deutsch' })
    .click();

  await expect(page).toHaveURL(/\/de\/helper\/payouts\/return$/);
  await expect(page.locator('html')).toHaveAttribute('lang', 'de');
  await expect(page.getByTestId('app-title')).toHaveText('Auszahlungen');
  await expect(page.getByRole('link', { name: 'Zurück', exact: true })).toBeVisible();
  await expect
    .poll(() => stub.calls.filter((c) => c.name === 'SetLocale'))
    .toEqual([{ name: 'SetLocale', variables: { locale: 'DE' } }]);
  // The account's language is saved; the menu now speaks German.
  await page.getByRole('button', { name: 'Konto und Sprache' }).click();
  await expect(
    page.getByRole('navigation', { name: 'Sprache' }).getByRole('link', { name: 'Deutsch' }),
  ).toHaveAttribute('aria-current', 'true');
});

test('"Sign out" in the menu ends the session and leaves the logged-in page', async ({
  page,
  stubGraphql,
}) => {
  const state = { signedIn: true };
  await stubGraphql(answers(state));
  const calls = await stubAuth(page, {
    'sign-out': () => ((state.signedIn = false), { data: { signOut: true } }),
  });
  await page.goto('/en/helper/payouts');
  await page.getByTestId('account-menu-button').click();
  await page.getByTestId('sign-out').click();

  await expect(page).toHaveURL(/\/en$/);
  await expect(page.getByTestId('site-header').getByTestId('sign-in-link')).toBeVisible();
  expect(calls).toEqual(['sign-out']);
});

test('"Log out of all devices" in the menu ends every session', async ({ page, stubGraphql }) => {
  const state = { signedIn: true };
  await stubGraphql(answers(state));
  const calls = await stubAuth(page, {
    'sign-out-everywhere': () => ((state.signedIn = false), { data: { signOutEverywhere: true } }),
  });
  await page.goto('/en/helper/payouts');
  await page.getByTestId('account-menu-button').click();
  await page.getByTestId('sign-out-everywhere').click();

  await expect(page).toHaveURL(/\/en$/);
  await expect(page.getByTestId('sign-in-link')).toBeVisible();
  expect(calls).toEqual(['sign-out-everywhere']);
});

test('a failed "Log out of all devices" says so and keeps the menu open', async ({
  page,
  stubGraphql,
}) => {
  await stubGraphql(answers({ signedIn: true }));
  await stubAuth(page, {
    'sign-out-everywhere': () => ({
      data: null,
      errors: [{ message: 'down', extensions: { code: 'INTERNAL' } }],
    }),
  });
  await page.goto('/en/helper/payouts');
  await page.getByTestId('account-menu-button').click();
  await page.getByTestId('sign-out-everywhere').click();

  const menu = page.getByTestId('account-menu');
  await expect(menu.getByRole('alert')).toBeVisible();
  await expect(menu.getByTestId('sign-out-everywhere')).toBeEnabled();
  await expect(page).toHaveURL(/\/en\/helper\/payouts$/);
});

test('signed out, the menu offers "Log in" back to this page, and the language', async ({
  page,
  stubGraphql,
}) => {
  await stubGraphql(answers({ signedIn: false }));
  await page.goto('/en/helper/payouts');
  await page.getByTestId('account-menu-button').click();
  const menu = page.getByTestId('account-menu');
  await expect(menu.getByTestId('sign-in-link')).toHaveAttribute(
    'href',
    '/en/sign-in?next=%2Fhelper%2Fpayouts',
  );
  await expect(menu.getByRole('navigation', { name: 'Language' })).toBeVisible();
  await expect(menu.getByTestId('sign-out')).toHaveCount(0);
});

test('public pages keep their header, with the session controls in the menu', async ({
  page,
  stubGraphql,
}) => {
  await stubGraphql(answers({ signedIn: true }));
  await page.goto('/en');
  const header = page.getByTestId('site-header');
  await expect(header.getByRole('navigation', { name: 'Language' })).toBeVisible();
  await expect(header.getByTestId('account-menu-button')).toBeVisible();
  await expect(page.getByTestId('signed-in-as')).toBeHidden();
  await expect(page.getByTestId('app-top-bar')).toHaveCount(0);
  await noHorizontalScroll(page);
});

import type { Page } from '@playwright/test';
import { expect, test, type Answers, type GraphqlStub } from './graphql-stub';

/**
 * The poster's offers page against a stubbed API (/api/graphql answered here): the list,
 * sorting, the comparison table and Accept leading to checkout, at the
 * mobile (375px) and desktop widths of playwright.config.ts. Nothing here reaches a backend
 * (e2e/graphql-stub.ts).
 */

const TASK = 'task-1';
const future = new Date(Date.now() + 5 * 24 * 3600 * 1000).toISOString();

function offer(id: string, extra: Record<string, unknown> = {}) {
  return {
    __typename: 'Offer',
    id,
    taskId: TASK,
    helperUserId: `helper-${id}`,
    helperIsTrader: false,
    helperBusinessName: null,
    helperBusinessOib: null,
    helperBusinessAddress: null,
    status: 'SENT',
    priceCents: 3_000,
    serviceFeeCents: 360,
    serviceFeeCreditCents: 0,
    serviceFeeScheduleVersion: '1',
    serviceFeePercent: 12,
    serviceFeeMinimumCents: 250,
    totalCents: 3_360,
    message: '',
    messageMasked: null,
    expiresAt: future,
    isMine: false,
    createdAt: '2026-11-03T10:00:00Z',
    updatedAt: '2026-11-03T10:00:00Z',
    ...extra,
  };
}

const task = {
  __typename: 'Task',
  id: TASK,
  status: 'OPEN',
  category: 'GARDEN',
  title: 'Mow lawn & trim hedge for Mama',
  description: '',
  approximateLat: 42.65,
  approximateLng: 18.09,
  serviceAreaName: 'Dubrovnik',
  windowStart: future,
  windowEnd: future,
  flexibleDate: false,
  budgetCents: 3_500,
  photos: [],
  isMine: true,
  createdAt: '2026-11-01T10:00:00Z',
  publishedAt: '2026-11-01T10:00:00Z',
  address: null,
  lat: null,
  lng: null,
  accessDetails: null,
  careContact: null,
  textMasked: false,
  holdReasons: [],
};

async function stubApi(stubGraphql: (answers: Answers) => Promise<GraphqlStub>) {
  const offers = [
    offer('withdrawn', { status: 'WITHDRAWN', priceCents: 2_500 }),
    offer('marko', { message: 'Dobar dan! I have my own mower and trimmer.' }),
    offer('stjepan', {
      priceCents: 2_800,
      helperIsTrader: true,
      helperBusinessName: 'Obrt Stjepan Popravci',
      helperBusinessOib: '00000000698',
      helperBusinessAddress: 'Obala Stjepana Radića 12, 20000 Dubrovnik, HR',
    }),
  ];
  return stubGraphql({
    TaskOffers: () => ({ data: { taskOffers: offers } }),
    Task: () => ({ data: { task } }),
    CheckoutPolicy: () => ({ data: { currentLegalDocument: null } }),
    Viewer: () => ({ data: { viewer: null } }),
    ApiVersion: () => ({ data: { apiVersion: 'test' } }),
  });
}

const cardIds = (page: Page) =>
  page
    .locator('[data-testid^="offer-card-"]')
    .evaluateAll((cards) => cards.map((card) => card.getAttribute('data-testid')));

async function expectNoHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
}

test('compares offers, sorts them and accepts one into checkout', async ({ page, stubGraphql }) => {
  await stubApi(stubGraphql);
  await page.goto(`/en/tasks/${TASK}/offers`);

  // The count is the page title in the top bar, and the page's only h1 (mockup 4a).
  const title = page.getByTestId('app-top-bar').getByRole('heading', { level: 1 });
  await expect(title).toHaveText('3 offers');
  await expect(page.getByRole('heading', { level: 1 })).toHaveCount(1);
  await expect(page.getByText('Mow lawn & trim hedge for Mama')).toBeVisible();
  expect(await cardIds(page)).toEqual([
    'offer-card-marko',
    'offer-card-stjepan',
    'offer-card-withdrawn',
  ]);
  await expect(page.getByTestId('offer-card-withdrawn')).toContainText('Offer withdrawn');
  await expect(page.getByTestId('offer-card-marko')).toContainText('“Dobar dan!');
  await expectNoHorizontalScroll(page);
  // The total the poster pays first, the helper's price below it (BEAV-191).
  await expect(page.getByTestId('offer-card-marko')).toContainText('in total, incl. service fee');
  await expect(page.getByTestId('offer-card-marko')).toContainText("Helper's price");
  // ZZP čl. 61(1) at offer level (BEAV-192): the trader's identity and registered address, the
  // private helper's notice, and how obligations are split.
  await expect(page.getByTestId('offer-card-stjepan')).toContainText(
    'Obrt Stjepan Popravci · OIB 00000000698',
  );
  await expect(page.getByTestId('offer-card-stjepan')).toContainText(
    'Obala Stjepana Radića 12, 20000 Dubrovnik, HR',
  );
  await expect(page.getByTestId('offer-card-marko')).toContainText('not a trader');
  await expect(page.getByTestId('offer-card-marko')).toContainText('TaskBeavo handles payment');
  // CRD art. 6a: the ranking parameters are one tap away from the offers.
  await expect(page.getByTestId('offers-ranking')).toHaveAttribute('href', '/en/ranking');

  await page.getByRole('button', { name: 'Lowest price' }).click();
  await expect(page.getByRole('button', { name: 'Lowest price' })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  expect((await cardIds(page))[0]).toBe('offer-card-stjepan');

  await page.getByRole('button', { name: 'Compare' }).click();
  await expect(title).toHaveText('Compare 2 offers');
  await expect(page.getByRole('heading', { level: 1 })).toHaveCount(1);
  await expect(page.getByRole('columnheader', { name: 'Total incl. service fee' })).toBeVisible();
  await expect(page.getByRole('rowheader', { name: "Helper's price" })).toBeVisible();
  await expectNoHorizontalScroll(page);
  await page.getByRole('button', { name: 'Back to list' }).click();

  await page
    .getByTestId('offer-card-marko')
    .getByRole('button', { name: /^Accept the offer of/ })
    .click();
  await expect(page).toHaveURL(new RegExp(`/en/tasks/${TASK}/offers/marko/checkout$`));
  // No policy in this stub, so checkout can't be paid (e2e/checkout.spec.ts covers paying).
  await expect(page.getByTestId('policy-unavailable')).toBeVisible();
  await expect(page.getByTestId('checkout-pay')).toBeDisabled();
});

test('the comparison fits in German and French', async ({ page, stubGraphql }) => {
  await stubApi(stubGraphql);
  for (const [locale, compare, accept] of [
    ['de', 'Vergleichen', 'Annehmen'],
    ['fr', 'Comparer', 'Accepter'],
  ] as const) {
    await page.goto(`/${locale}/tasks/${TASK}/offers`);
    await expect(page.getByTestId('offer-card-marko')).toContainText(accept);
    await expectNoHorizontalScroll(page);
    await page.getByRole('button', { name: compare }).click();
    await expect(page.getByTestId('offers-compare')).toBeVisible();
    await expectNoHorizontalScroll(page);
  }
});

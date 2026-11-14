import type { Page } from '@playwright/test';
import { expect, test, type Answers } from './graphql-stub';

/**
 * The booking detail page (BEAV-115, mockup 05) against a stubbed API, at the mobile (375px)
 * and desktop widths of playwright.config.ts. Who sees the exact address is the server's
 * decision (backend bookings/tests/test_booking_detail.py); here the page shows what it gets.
 */

const ADDRESS = 'Ulica Ante Starčevića 24, Gruž';

function booking(helperUserId: string, { exact = true, ...extra }: Record<string, unknown> = {}) {
  return {
    __typename: 'Booking',
    id: 'booking-1',
    taskId: 'task-1',
    offerId: 'offer-1',
    helperUserId,
    helperIsTrader: false,
    status: 'FUNDS_HELD',
    amountCents: 3_000,
    feeCents: 360,
    creditCents: 0,
    totalCents: 3_360,
    slotStart: '2026-11-14T08:00:00Z',
    termsVersion: 'draft-1',
    cancellationVersion: '2026-11-c',
    checkoutExpiresAt: null,
    lastPaymentAttempt: null,
    checkoutFailureReason: null,
    createdAt: '2026-11-05T09:59:00Z',
    updatedAt: '2026-11-05T09:59:00Z',
    task: {
      __typename: 'Task',
      id: 'task-1',
      title: 'Grocery run for Mama',
      category: null,
      serviceAreaName: 'Dubrovnik',
      address: exact ? ADDRESS : null,
      accessDetails: exact ? 'Doorbell Kovač, 2nd floor, gate 4417' : null,
      onSiteContact: exact
        ? {
            __typename: 'OnSiteContact',
            id: 'contact-1',
            name: 'Mama Kata',
            area: 'Gruž',
            language: 'HR',
            phone: '+385912345678',
          }
        : null,
    },
    helperBusiness: null,
    cancelPreview: null,
    helperCancelPreview: null,
    withdrawalAvailable: false,
    canRequestNoFault: false,
    noFaultRequest: null,
    cancellation: null,
    arrival: { __typename: 'BookingArrival', id: 'booking-1', noShowContestBy: null },
    ...extra,
  };
}

const viewer = {
  __typename: 'User',
  id: 'user-1',
  email: 'marko@example.com',
  firstName: 'Marko',
  lastName: 'Perić',
  locale: 'en',
  country: 'HR',
  timeZone: 'Europe/Zagreb',
  phoneVerified: true,
  phoneNumberMasked: null,
};

const answers = (bookingNow: () => unknown): Answers => ({
  BookingDetail: () => ({ data: { booking: bookingNow() } }),
  Viewer: () => ({ data: { viewer } }),
  TermsStatus: () => ({
    data: {
      viewer: { __typename: 'User', id: viewer.id, mustAcceptTerms: false, termsToAccept: null },
    },
  }),
  ApiVersion: () => ({ data: { apiVersion: 'test' } }),
});

const noHorizontalScroll = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);

test('shows the active helper the place and links to the day of the job, unindexed', async ({
  page,
  stubGraphql,
}) => {
  const stub = await stubGraphql(answers(() => booking('user-1')));
  await page.goto('/en/bookings/booking-1');

  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Booking');
  await expect(page.getByTestId('booking-address')).toHaveText(ADDRESS);
  await expect(page.getByText('Shown to you only while booked')).toBeVisible();
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute('content', 'noindex, nofollow');
  await expect(page.getByTestId('booking-link-arrival')).toHaveAttribute(
    'href',
    '/en/bookings/booking-1/arrival',
  );
  await expect.poll(() => stub.subscriptions).toContain('BookingDetailUpdated');
});

test('shows only the area once the server stops sending the address', async ({
  page,
  stubGraphql,
}) => {
  await stubGraphql(answers(() => booking('user-1', { status: 'CLOSED', exact: false })));
  await page.goto('/en/bookings/booking-1');

  await expect(page.getByTestId('booking-area')).toHaveText('Area: Dubrovnik');
  await expect(page.getByTestId('booking-address')).toHaveCount(0);
  await expect(page.getByText('Mama Kata')).toHaveCount(0);
});

test('the page fits at every width in German and French', async ({ page, stubGraphql }) => {
  await stubGraphql(answers(() => booking('poster-1', { status: 'PAYMENT_REQUESTED' })));
  for (const [locale, release] of [
    ['de', 'Zahlung freigeben'],
    ['fr', 'Verser le paiement'],
  ] as const) {
    await page.goto(`/${locale}/bookings/booking-1`);
    await expect(page.getByTestId('booking-link-release')).toHaveText(release);
    expect(await noHorizontalScroll(page)).toBe(true);
  }
});

// BEAV-212: mockups 7b and 18a.
test('the active helper gets the contact card with a Call button, and the summary card', async ({
  page,
  stubGraphql,
}) => {
  await stubGraphql(answers(() => booking('user-1')));
  await page.goto('/en/bookings/booking-1');

  const summary = page.getByTestId('booking-summary');
  await expect(summary).toContainText('Grocery run for Mama');
  await expect(page.getByTestId('booking-amount')).toContainText('You get');
  await expect(page.getByTestId('booking-amount')).toContainText('€30.00');
  const contact = page.getByTestId('booking-contact');
  await expect(contact).toContainText('On-site contact: Mama Kata, Gruž');
  await expect(contact.getByTestId('booking-call')).toHaveAttribute('href', 'tel:+385912345678');
  const call = await contact.getByTestId('booking-call').boundingBox();
  expect(call!.height).toBeGreaterThanOrEqual(44);
});

test('"Payment requested" has its own card for the helper (7b)', async ({ page, stubGraphql }) => {
  await stubGraphql(answers(() => booking('user-1', { status: 'PAYMENT_REQUESTED' })));
  await page.goto('/en/bookings/booking-1');
  await expect(page.getByTestId('booking-requested')).toContainText(
    'Waiting for the poster to release €30.00.',
  );
});

test('the helper page with the contact card fits in all four languages', async ({
  page,
  stubGraphql,
}) => {
  await stubGraphql(answers(() => booking('user-1', { status: 'PAYMENT_REQUESTED' })));
  for (const locale of ['hr', 'en', 'de', 'fr'] as const) {
    await page.goto(`/${locale}/bookings/booking-1`);
    await expect(page.getByTestId('booking-contact')).toBeVisible();
    expect(await noHorizontalScroll(page), locale).toBe(true);
  }
});

test('at desktop width the page is a centred column', async ({ page, stubGraphql }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'a desktop-width check');
  await stubGraphql(answers(() => booking('user-1')));
  await page.goto('/en/bookings/booking-1');
  const box = (await page.getByTestId('booking-summary').boundingBox())!;
  expect(box.width).toBeLessThanOrEqual(560);
  const centre = box.x + box.width / 2;
  expect(Math.abs(centre - 640)).toBeLessThan(24);
});

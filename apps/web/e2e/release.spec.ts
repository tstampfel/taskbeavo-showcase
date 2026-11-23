import type { Page } from '@playwright/test';
import { expect, test, type Answers } from './graphql-stub';

/**
 * Request and release payment on the web (BEAV-118, mockups 7a, 7d–7f) against a stubbed API.
 * The amounts and flags are the server's (backend bookings/tests/test_release.py); here the
 * pages show what they get and send the right mutations. The e2e build has no Stripe key, so
 * a tip ends at the card form's "not set up" line, after addTip.
 */

const POSTER = 'poster-1';
const HELPER = 'helper-1';

function booking(extra: Record<string, unknown> = {}) {
  return {
    __typename: 'Booking',
    id: 'booking-1',
    taskId: 'task-1',
    offerId: 'offer-1',
    helperUserId: HELPER,
    helperIsTrader: false,
    status: 'PAYMENT_REQUESTED',
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
      address: null,
      accessDetails: null,
      onSiteContact: null,
    },
    helperBusiness: null,
    cancelPreview: null,
    helperCancelPreview: null,
    withdrawalAvailable: false,
    canRequestNoFault: false,
    noFaultRequest: null,
    cancellation: null,
    arrival: {
      __typename: 'BookingArrival',
      id: 'booking-1',
      noShowContestBy: null,
      canAnswerLate: false,
      canConfirmCame: false,
      canConfirmNoShow: false,
    },
    canRequestPayment: false,
    invoiceRequired: false,
    paymentRequest: {
      __typename: 'PaymentRequest',
      id: 'pr-1',
      requestedAt: '2026-11-14T10:10:00Z',
      note: 'All delivered. Mama says hvala!',
      invoiceJir: null,
      invoice: null,
      photos: [],
    },
    canReleasePayment: true,
    releaseProposal: null,
    tip: null,
    ...extra,
  };
}

const proposal = (status: string, refundCents: number | null = 1_120) => ({
  __typename: 'ReleaseProposal',
  id: 'rp-1',
  releasedCents: 2_000,
  status,
  refundCents,
  createdAt: '2026-11-14T11:00:00Z',
  answeredAt: null,
});

const viewer = (id: string) => ({
  __typename: 'User',
  id,
  email: 'someone@example.com',
  firstName: 'Ana',
  lastName: 'Kovač',
  locale: 'en',
  country: 'AT',
  timeZone: 'Europe/Vienna',
  phoneVerified: true,
  phoneNumberMasked: null,
});

/** The stub; `state.booking` is what the next BookingDetail returns, as on the server. */
function answers(
  state: { booking: Record<string, unknown> },
  viewerId: string,
  more: Answers = {},
) {
  return {
    BookingDetail: () => ({ data: { booking: state.booking } }),
    Viewer: () => ({ data: { viewer: viewer(viewerId) } }),
    TermsStatus: () => ({
      data: {
        viewer: { __typename: 'User', id: viewerId, mustAcceptTerms: false, termsToAccept: null },
      },
    }),
    ApiVersion: () => ({ data: { apiVersion: 'test' } }),
    ...more,
  } satisfies Answers;
}

const noHorizontalScroll = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);

test('the poster releases with a tip after the confirmation, then pays the tip (7d–7f)', async ({
  page,
  stubGraphql,
}) => {
  const state = { booking: booking() };
  const stub = await stubGraphql(
    answers(state, POSTER, {
      ReleasePayment: () => {
        state.booking = booking({ status: 'RELEASED', canReleasePayment: false });
        return { data: { releasePayment: state.booking } };
      },
      AddTip: () => ({
        data: {
          addTip: {
            __typename: 'TipSession',
            id: 'tip-1',
            clientSecret: 'pi_e2e_secret_e2e',
            tip: { __typename: 'Tip', id: 'tip-1', amountCents: 500, status: 'PENDING' },
          },
        },
      }),
    }),
  );
  await page.goto('/en/bookings/booking-1/release');

  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Release payment');
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute('content', 'noindex, nofollow');
  await expect(page.getByText('All delivered. Mama says hvala!')).toBeVisible();
  await expect(page.getByTestId('release-amount')).toHaveText('€30.00');
  // No tip amount is preselected.
  await expect(page.getByTestId('release-tip-none')).toBeChecked();
  for (const cents of [200, 500, 1000]) {
    await expect(page.getByTestId(`release-tip-${cents}`)).not.toBeChecked();
  }
  const chip = await page
    .locator('label', { has: page.getByTestId('release-tip-500') })
    .boundingBox();
  expect(chip!.height).toBeGreaterThanOrEqual(44);

  await page.locator('label', { has: page.getByTestId('release-tip-500') }).click();
  await page.getByTestId('release-submit').click();
  const confirm = page.getByRole('dialog');
  // releasePayment pays the task price; the tip is its own card payment afterwards (R1-3).
  await expect(confirm.getByRole('heading')).toHaveText('Release €30.00 to your helper?');
  await expect(confirm.getByTestId('release-confirm-tip')).toHaveText('€5.00');
  await expect(confirm.getByTestId('release-confirm-tip-note')).toHaveText(
    'You pay the tip by card right after releasing.',
  );
  expect(stub.calls.map((c) => c.name)).not.toContain('ReleasePayment');

  await confirm.getByTestId('release-confirm-release').click();

  await expect(page.getByTestId('release-released')).toContainText('Payment released!');
  await expect(page.getByTestId('release-released')).toContainText('€30.00 is on its way');
  await expect(page.getByTestId('release-tip-not-configured')).toBeVisible();
  expect(stub.calls.find((c) => c.name === 'ReleasePayment')?.variables).toEqual({
    bookingId: 'booking-1',
  });
  expect(stub.calls.find((c) => c.name === 'AddTip')?.variables).toEqual({
    bookingId: 'booking-1',
    amountCents: 500,
  });
});

test('the poster proposes a lower amount after the server preview', async ({
  page,
  stubGraphql,
}) => {
  const state = { booking: booking() };
  const stub = await stubGraphql(
    answers(state, POSTER, {
      PartialReleasePreview: () => ({
        data: {
          partialReleasePreview: {
            __typename: 'PartialReleasePreview',
            id: 'booking-1',
            releasedCents: 2_000,
            keptFeeCents: 240,
            refundCents: 1_120,
          },
        },
      }),
      ProposePartialRelease: () => {
        state.booking = booking({ releaseProposal: proposal('OPEN') });
        return { data: { proposePartialRelease: state.booking } };
      },
    }),
  );
  await page.goto('/en/bookings/booking-1/release');

  await page.getByTestId('release-lower').click();
  const sheet = page.getByRole('dialog', { name: 'Release a lower amount' });
  await sheet.getByLabel('Amount for your helper (€)').fill('30');
  await sheet.getByTestId('release-partial-check').click();
  await expect(sheet.getByRole('alert')).toContainText('less than €30.00');

  await sheet.getByLabel('Amount for your helper (€)').fill('20,00');
  await sheet.getByTestId('release-partial-check').click();
  await expect(sheet.getByTestId('release-partial-refund')).toHaveText('€11.20');
  await sheet.getByTestId('release-partial-send').click();

  await expect(page.getByTestId('release-proposal-open')).toContainText(
    'You proposed €20.00 instead of €30.00.',
  );
  expect(stub.calls.find((c) => c.name === 'ProposePartialRelease')?.variables).toEqual({
    bookingId: 'booking-1',
    releasedCents: 2_000,
  });
});

test('the helper accepts the lower amount', async ({ page, stubGraphql }) => {
  const state = {
    booking: booking({ canReleasePayment: false, releaseProposal: proposal('OPEN', null) }),
  };
  const stub = await stubGraphql(
    answers(state, HELPER, {
      RespondToPartialRelease: () => {
        state.booking = booking({
          canReleasePayment: false,
          releaseProposal: proposal('ACCEPTED', null),
        });
        return { data: { respondToPartialRelease: state.booking } };
      },
    }),
  );
  await page.goto('/en/bookings/booking-1/release');

  await expect(page.getByTestId('release-answer')).toContainText(
    'They propose €20.00 instead of €30.00.',
  );
  await expect(page.getByTestId('release-submit')).toHaveCount(0);
  await page.getByTestId('release-accept').click();

  await expect(page.getByTestId('release-partial-settled')).toContainText(
    "You accepted €20.00. It's on its way to your bank account.",
  );
  expect(stub.calls.find((c) => c.name === 'RespondToPartialRelease')?.variables).toEqual({
    bookingId: 'booking-1',
    accept: true,
  });
});

test('the helper requests payment on the web with a note (7a)', async ({ page, stubGraphql }) => {
  const state = {
    booking: booking({
      status: 'FUNDS_HELD',
      canReleasePayment: false,
      canRequestPayment: true,
      paymentRequest: null,
    }),
  };
  const stub = await stubGraphql(
    answers(state, HELPER, {
      RequestPayment: () => {
        state.booking = booking({ canReleasePayment: false });
        return { data: { requestPayment: state.booking } };
      },
    }),
  );
  await page.goto('/en/bookings/booking-1');
  await page.getByTestId('booking-link-request-payment').click();

  await expect(page).toHaveURL(/\/en\/bookings\/booking-1\/request-payment$/);
  await expect(page.getByTestId('request-payment-price')).toHaveText('€30.00');
  await expect(page.getByTestId('request-payment-photos-in-app')).toBeVisible();
  await page.getByLabel('Note (optional)').fill('  All delivered  ');
  await page.getByTestId('request-payment-submit').click();

  await expect(page.getByTestId('payment-requested')).toContainText(
    'Waiting for the poster to release €30.00.',
  );
  expect(stub.calls.find((c) => c.name === 'RequestPayment')?.variables).toEqual({
    input: { bookingId: 'booking-1', note: 'All delivered', photoIds: null },
  });
});

test('the release page fits in all four languages', async ({ page, stubGraphql }) => {
  const state = { booking: booking() };
  await stubGraphql(answers(state, POSTER));
  for (const [locale, title] of [
    ['hr', 'Oslobodi isplatu'],
    ['en', 'Release payment'],
    ['de', 'Zahlung freigeben'],
    ['fr', 'Verser le paiement'],
  ] as const) {
    await page.goto(`/${locale}/bookings/booking-1/release`);
    await expect(page.getByRole('heading', { level: 1 })).toHaveText(title);
    await expect(page.getByTestId('release-submit')).toHaveText(title);
    expect(await noHorizontalScroll(page), locale).toBe(true);
  }
});

test('back from the bank after a tip: confirming, or the decline, and no client secret left in the address (R1-2)', async ({
  page,
  stubGraphql,
}) => {
  const tip = { __typename: 'Tip', id: 'tip-1', amountCents: 500, status: 'PENDING' };
  const state = { booking: booking({ status: 'RELEASED', canReleasePayment: false, tip }) };
  await stubGraphql(answers(state, POSTER));
  const stripeParams =
    'payment_intent=pi_e2e&payment_intent_client_secret=pi_e2e_secret_e2e&redirect_status=';

  await page.goto(`/en/bookings/booking-1/release?${stripeParams}succeeded`);
  await expect(page.getByTestId('release-tip-confirming')).toContainText('Confirming your tip');
  await expect(page).toHaveURL(/\/en\/bookings\/booking-1\/release$/);

  await page.goto(`/en/bookings/booking-1/release?${stripeParams}failed`);
  await expect(page.getByTestId('release-tip-error')).toBeVisible();
  await expect(page.getByTestId('release-tip-pay')).toBeVisible();
  await expect(page).toHaveURL(/\/en\/bookings\/booking-1\/release$/);
});

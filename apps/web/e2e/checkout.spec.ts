import type { Page } from '@playwright/test';
import { expect, test, WS_TOKEN, type Answers, type GraphqlStub } from './graphql-stub';

/**
 * Checkout against a stubbed API (/api/graphql answered here), at the mobile (375px) and
 * desktop widths of playwright.config.ts: the two consent boxes worded by the legal document,
 * acceptOffer with the version shown, and every state after the card form. This server has
 * no Stripe key, so the card form itself (Payment Element, 4242 and 3D Secure cards) is not
 * exercised here; that is a manual test-mode run. Nothing here reaches a backend, so a local
 * Django on :8000 changes nothing (e2e/graphql-stub.ts).
 */

const future = new Date(Date.now() + 5 * 24 * 3600 * 1000).toISOString();
const inTwentyMinutes = () => new Date(Date.now() + 20 * 60 * 1000).toISOString();
const POLICY_TICK = 'I accept the cancellation policy, version 2026-11-c.';
const EARLY_TICK = 'I expressly ask for the service to start before the withdrawal period ends.';

const offer = {
  __typename: 'Offer',
  id: 'marko',
  taskId: 'task-1',
  helperUserId: '7',
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
};

const task = {
  __typename: 'Task',
  id: 'task-1',
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
  onSiteContact: null,
  careContact: null,
  textMasked: false,
  holdReasons: [],
};

function policy(language: string) {
  return {
    __typename: 'LegalDocument',
    id: `doc-${language}`,
    kind: 'CANCELLATION',
    version: '2026-11-c',
    language,
    effectiveAt: '2026-11-01T00:00:00Z',
    text: 'DRAFT – the full cancellation policy.',
    checkoutConsent: {
      __typename: 'CheckoutConsentTexts',
      privateHelper: {
        __typename: 'CheckoutConsentText',
        cancellationPolicy: POLICY_TICK,
        earlyStart: EARLY_TICK,
      },
      traderHelper: {
        __typename: 'CheckoutConsentText',
        cancellationPolicy: POLICY_TICK,
        earlyStart: 'I ask a registered business to start before my withdrawal period ends.',
      },
    },
  };
}

function booking(extra: Record<string, unknown> = {}) {
  return {
    __typename: 'Booking',
    id: 'booking-1',
    taskId: 'task-1',
    offerId: 'marko',
    helperUserId: '7',
    helperIsTrader: false,
    status: 'AWAITING_PAYMENT',
    amountCents: 3_000,
    feeCents: 360,
    creditCents: 0,
    totalCents: 3_360,
    slotStart: null,
    termsVersion: 'draft-1',
    cancellationVersion: '2026-11-c',
    checkoutExpiresAt: inTwentyMinutes(),
    lastPaymentAttempt: null,
    checkoutFailureReason: null,
    createdAt: '2026-11-05T09:59:00Z',
    updatedAt: '2026-11-05T09:59:00Z',
    ...extra,
  };
}

interface Stub {
  /** The variables each operation was last sent with. */
  calls: Record<string, Record<string, unknown>>;
  graphql: GraphqlStub;
}

async function stubApi(
  stubGraphql: (answers: Answers) => Promise<GraphqlStub>,
  bookingNow = () => booking(),
  offerNow: Record<string, unknown> = offer,
): Promise<Stub> {
  const answers: Answers = {
    TaskOffers: () => ({ data: { taskOffers: [offerNow] } }),
    Task: () => ({ data: { task } }),
    CheckoutPolicy: (variables) => ({
      data: { currentLegalDocument: policy(String(variables.language ?? 'EN')) },
    }),
    AcceptOffer: () => ({
      data: {
        acceptOffer: {
          __typename: 'CheckoutSession',
          id: 'booking-1',
          clientSecret: 'pi_123_secret_456',
          paymentPending: false,
          booking: bookingNow(),
        },
      },
    }),
    Booking: () => ({ data: { booking: bookingNow() } }),
    Viewer: () => ({ data: { viewer: null } }),
    ApiVersion: () => ({ data: { apiVersion: 'test' } }),
  };
  const graphql = await stubGraphql(answers);
  const stub: Stub = {
    get calls() {
      return Object.fromEntries(graphql.calls.map((call) => [call.name, call.variables]));
    },
    graphql,
  };
  return stub;
}

const noHorizontalScroll = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);

test('pays only with both boxes ticked, with the policy version shown', async ({
  page,
  stubGraphql,
}) => {
  const stub = await stubApi(stubGraphql);
  await page.goto('/en/tasks/task-1/offers/marko/checkout');

  await expect(page.getByRole('heading', { level: 1, name: 'Checkout' })).toBeVisible();
  await expect(page.getByTestId('checkout-summary')).toContainText('€33.60');
  const policyBox = page.getByRole('checkbox', { name: POLICY_TICK });
  const earlyBox = page.getByRole('checkbox', { name: EARLY_TICK });
  await expect(policyBox).not.toBeChecked();
  await expect(earlyBox).not.toBeChecked();
  const pay = page.getByRole('button', { name: 'Book and pay €33.60' });
  await expect(pay).toBeDisabled();
  // The fee notice from the API's amounts, on the same screen as the boxes (BEAV-191).
  await expect(page.getByTestId('checkout-fee-notice')).toContainText(
    'Task price €30.00 (paid to the helper) + TaskBeavo service fee €3.60 (12%, min. €2.50) = total €33.60',
  );
  await expect(page.getByTestId('checkout-total-due')).toHaveText('Total to pay: €33.60');
  // Mockup 6a (BEAV-210): the booking summary names the task and the helper type, the money is
  // held via Stripe, and the order button bar stays on screen before scrolling.
  await expect(page.getByTestId('checkout-summary')).toContainText('Private individual');
  await expect(page.getByTestId('checkout-secure')).toHaveText(
    'Payment protected · held via Stripe',
  );
  await expect(pay).toBeInViewport();

  await page.getByTestId('policy-open').click();
  await expect(page.getByRole('dialog')).toContainText('the full cancellation policy');
  await page.keyboard.press('Escape');

  await policyBox.check();
  await expect(pay).toBeDisabled();
  await earlyBox.check();
  await pay.click();

  await expect(page).toHaveURL(/\/en\/bookings\/booking-1\/checkout$/);
  expect(stub.calls.AcceptOffer).toMatchObject({
    offerId: 'marko',
    expectedPriceCents: 3_000,
    expectedTotalCents: 3_360,
    expectedFeeScheduleVersion: '1',
    consent: {
      cancellationPolicyAccepted: true,
      earlyStartRequested: true,
      cancellationVersion: '2026-11-c',
      platform: 'WEB',
      language: 'EN',
      variant: 'PRIVATE_HELPER',
    },
  });
  // The client secret never goes in the URL.
  expect(page.url()).not.toContain('secret');
  // No Stripe key on this server: the form says so, and nothing claims a payment.
  await expect(page.getByTestId('checkout-not-configured')).toBeVisible();
  await expect(page.getByTestId('checkout-held')).toHaveCount(0);
  expect(await noHorizontalScroll(page)).toBe(true);
});

test('shows held only once the server reports FUNDS_HELD', async ({ page, stubGraphql }) => {
  await stubApi(stubGraphql, () => booking({ status: 'FUNDS_HELD' }));
  await page.goto('/en/bookings/booking-1/checkout');

  await expect(page.getByTestId('checkout-held')).toContainText('€33.60 held safely via Stripe');
  await expect(page.getByTestId('checkout-held')).toContainText(
    "We've emailed your booking confirmation",
  );
});

test('a declined card stays on the form with the reason', async ({ page, stubGraphql }) => {
  await stubApi(stubGraphql, () =>
    booking({
      lastPaymentAttempt: {
        __typename: 'PaymentAttempt',
        id: 'attempt-1',
        kind: 'DECLINED',
        declineCode: 'insufficient_funds',
        at: new Date().toISOString(),
      },
    }),
  );
  await page.goto('/en/bookings/booking-1/checkout');

  await expect(page.getByTestId('checkout-declined')).toContainText(
    'Payment declined. Try another card',
  );
  await expect(page.getByTestId('checkout-declined')).toContainText(
    "The card doesn't have enough funds. Nothing was charged.",
  );
  await expect(page.getByTestId('checkout-expiry')).toContainText('Checkout stays open');
});

test('back from a bank that failed, shows the decline instead of confirming', async ({
  page,
  stubGraphql,
}) => {
  // The webhook's decline is already stored when the poster lands (review R1-2).
  await stubApi(stubGraphql, () =>
    booking({
      lastPaymentAttempt: {
        __typename: 'PaymentAttempt',
        id: 'attempt-1',
        kind: 'DECLINED',
        declineCode: 'authentication_required',
        at: new Date(Date.now() - 60_000).toISOString(),
      },
    }),
  );
  await page.goto('/en/bookings/booking-1/checkout?payment_intent=pi_123&redirect_status=failed');

  await expect(page.getByTestId('checkout-declined').first()).toContainText('Payment declined');
  await expect(page.getByTestId('checkout-confirming')).toHaveCount(0);
  await expect(page.getByTestId('checkout-expiry')).toBeVisible();
  // Stripe's return parameters are dropped from the address.
  await expect(page).toHaveURL(/\/en\/bookings\/booking-1\/checkout$/);
});

test('back from a bank that succeeded, confirms until FUNDS_HELD', async ({
  page,
  stubGraphql,
}) => {
  await stubApi(stubGraphql);
  await page.goto(
    '/en/bookings/booking-1/checkout?payment_intent=pi_123&redirect_status=succeeded',
  );

  await expect(page.getByTestId('checkout-confirming')).toBeVisible();
  await expect(page.getByTestId('checkout-held')).toHaveCount(0);
});

for (const [reason, testId, text] of [
  ['CHECKOUT_EXPIRED', 'checkout-expired', 'Checkout timed out'],
  ['OFFER_WITHDRAWN', 'checkout-withdrawn', 'The helper withdrew their offer'],
] as const) {
  test(`an ended checkout (${reason}) says what happened`, async ({ page, stubGraphql }) => {
    await stubApi(stubGraphql, () =>
      booking({ status: 'PAYMENT_FAILED', checkoutFailureReason: reason }),
    );
    await page.goto('/en/bookings/booking-1/checkout');

    await expect(page.getByTestId(testId)).toContainText(text);
    await page.getByTestId(testId).getByRole('button').click();
    await expect(page).toHaveURL(/\/en\/tasks\/task-1\/offers$/);
  });
}

test('the consent and pay button fit in German and French', async ({ page, stubGraphql }) => {
  await stubApi(stubGraphql);
  for (const [locale, pay] of [
    ['de', 'Zahlungspflichtig buchen'],
    ['fr', 'Commande avec obligation de paiement'],
  ] as const) {
    await page.goto(`/${locale}/tasks/task-1/offers/marko/checkout`);
    await expect(page.getByTestId('checkout-consent')).toBeVisible();
    await expect(page.getByTestId('checkout-pay')).toContainText(pay);
    expect(await noHorizontalScroll(page)).toBe(true);
  }
});

test('the booking subscription connects to the stub, never a local API', async ({
  page,
  stubGraphql,
}) => {
  // The socket's token request carries no variables; with a Django on :8000 it used to crash
  // the stub (BEAV-193). Here the socket is always stubbed, so that path runs every time.
  const stub = await stubApi(stubGraphql);
  await page.goto('/en/bookings/booking-1/checkout');

  await expect(page.getByTestId('checkout-expiry')).toBeVisible();
  await expect.poll(() => stub.graphql.subscriptions).toContain('BookingUpdated');
  expect(stub.graphql.calls).toContainEqual({ name: 'WebSocketToken', variables: {} });
  expect(stub.graphql.socketInits[0]).toMatchObject({ authToken: WS_TOKEN });
});

/** True when the two boxes overlap on screen. */
function overlaps(a: { y: number; height: number }, b: { y: number; height: number }) {
  return a.y < b.y + b.height && b.y < a.y + a.height;
}

const traderOffer = {
  ...offer,
  helperIsTrader: true,
  helperBusinessName: 'Obrt Stjepan Popravci',
  helperBusinessOib: '00000000698',
  helperBusinessAddress: 'Obala Stjepana Radića 12, 20000 Dubrovnik, HR',
};

// The order bar is pinned only while it fits (review R1-1): with 200% text it must never cover
// the trader details or a focused consent box, in any language, on a 375px phone
// (WCAG 2.2 2.4.11 Focus Not Obscured).
for (const height of [812, 667]) {
  test(`with 200% text at 375×${height}, the order bar never covers the boxes or trader details`, async ({
    page,
    stubGraphql,
  }, testInfo) => {
    test.skip(testInfo.project.name !== 'mobile', 'a phone-width check');
    await stubApi(stubGraphql, () => booking(), traderOffer);
    await page.setViewportSize({ width: 375, height });
    // The browser's default font size, as the text-size setting changes it: 16px → 32px.
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Page.setFontSizes', { fontSizes: { standard: 32, fixed: 26 } });

    for (const locale of ['hr', 'en', 'de', 'fr'] as const) {
      await page.goto(`/${locale}/tasks/task-1/offers/marko/checkout`);
      const foot = page.getByTestId('checkout-foot');
      await expect(page.getByTestId('checkout-consent')).toBeVisible();
      // Too tall to pin with this text size: it stays at the end of the page.
      await expect(foot).toHaveAttribute('data-pinned', 'false');

      const trader = page.getByTestId('trader-identity');
      await trader.scrollIntoViewIfNeeded();
      expect(overlaps((await trader.boundingBox())!, (await foot.boundingBox())!), locale).toBe(
        false,
      );
      for (const box of ['consent-cancellation-policy', 'consent-early-start']) {
        await page.getByTestId(box).focus();
        const boxRect = (await page.getByTestId(box).boundingBox())!;
        expect(overlaps(boxRect, (await foot.boundingBox())!), `${locale} ${box}`).toBe(false);
      }
      expect(await noHorizontalScroll(page)).toBe(true);
    }
  });
}

test('at the default text size the order bar is pinned and a focused box scrolls clear of it', async ({
  page,
  stubGraphql,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'mobile', 'a phone-width check');
  await stubApi(stubGraphql);
  await page.setViewportSize({ width: 375, height: 667 });
  await page.goto('/en/tasks/task-1/offers/marko/checkout');
  const foot = page.getByTestId('checkout-foot');
  await expect(foot).toHaveAttribute('data-pinned', 'true');
  await expect(page.getByTestId('checkout-pay')).toBeInViewport();
  for (const box of ['consent-cancellation-policy', 'consent-early-start']) {
    await page.getByTestId(box).focus();
    const boxRect = (await page.getByTestId(box).boundingBox())!;
    expect(overlaps(boxRect, (await foot.boundingBox())!), box).toBe(false);
  }
});

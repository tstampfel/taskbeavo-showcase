import type { Locator, Page } from '@playwright/test';
import { expect, test, type Answers, type Variables } from './graphql-stub';

/**
 * The cancel page (mockup 13, BEAV-211) against a stubbed API, at the mobile (375px) and
 * desktop widths of playwright.config.ts: the reason first, then "Your refund" with the
 * server's amounts. Every amount is the API's (bookings/cancellation.py, rule g); the page
 * only formats it, so the stub's cents must come back as the same euros.
 */

const late = {
  __typename: 'CancelPreview',
  id: 'booking-1',
  case: 'LATE',
  chargeCents: 3_360,
  refundCents: 2_521,
  helperCompensationCents: 750,
  keptFeeCents: 89,
  creditBackCents: 0,
  refusedWith: null,
};

function booking(extra: Record<string, unknown> = {}) {
  return {
    __typename: 'Booking',
    id: 'booking-1',
    taskId: 'task-1',
    offerId: 'offer-1',
    helperUserId: 'helper-1',
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
    withdrawalAvailable: false,
    withdrawalEndsAt: null,
    cancelPreview: late,
    cancellation: null,
    helperCancelPreview: null,
    noFaultRequest: null,
    canRequestNoFault: true,
    canReopenTask: false,
    taskReopened: false,
    ...extra,
  };
}

const viewer = {
  __typename: 'User',
  id: 'poster-1',
  email: 'ana@example.com',
  firstName: 'Ana',
  lastName: 'Primjer',
  locale: 'en',
  country: 'AT',
  timeZone: 'Europe/Vienna',
  phoneVerified: true,
  phoneNumberMasked: null,
};

const answers = (bookingNow: () => unknown, extra: Answers = {}): Answers => ({
  BookingCancellation: () => ({ data: { booking: bookingNow() } }),
  Viewer: () => ({ data: { viewer } }),
  TermsStatus: () => ({
    data: {
      viewer: { __typename: 'User', id: viewer.id, mustAcceptTerms: false, termsToAccept: null },
    },
  }),
  ApiVersion: () => ({ data: { apiVersion: 'test' } }),
  ...extra,
});

const noHorizontalScroll = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);

/** True when the two boxes overlap on screen. */
function overlaps(a: { y: number; height: number }, b: { y: number; height: number }) {
  return a.y < b.y + b.height && b.y < a.y + a.height;
}

test('the reason first, then the refund with the API amounts, Back keeps the reason', async ({
  page,
  stubGraphql,
}) => {
  const cancelled: Variables[] = [];
  await stubGraphql(
    answers(() => booking(), {
      CancelBooking: (variables) => {
        cancelled.push(variables);
        return { data: { cancelBooking: booking() } };
      },
    }),
  );
  await page.goto('/en/bookings/booking-1/cancel');

  // Step 1 (13b): the reasons in a card, the no-fault ones in their own, no amounts yet.
  await expect(page.getByTestId('cancel-step-reason')).toBeVisible();
  await expect(page.getByRole('group', { name: 'Why are you cancelling?' })).toBeVisible();
  await expect(page.getByRole('group', { name: "Nobody's fault?" })).toBeVisible();
  await expect(page.getByTestId('cancel-refund-rows')).toHaveCount(0);
  const next = page.getByTestId('cancel-see-refund');
  await expect(next).toBeDisabled();
  await page.getByTestId('cancel-reason-NO_LONGER_NEEDED').check();
  await next.click();

  // Step 2 (13c): "Your refund", focused, with the server's amounts unchanged.
  const refund = page.getByTestId('cancel-step-refund');
  await expect(refund.getByRole('heading', { name: 'Your refund' })).toBeFocused();
  const rows = page.getByTestId('cancel-refund-rows');
  await expect(rows).toContainText('€25.21');
  await expect(rows).toContainText('€7.50');
  await expect(rows).toContainText('€0.89');
  await expect(rows).toContainText('€33.60');
  await expect(refund).toContainText('the helper keeps part of the price');
  await expect(refund).toContainText('Cancelling now is final');

  // Back to the reasons: the pick is kept.
  await page.getByTestId('cancel-step-back').click();
  await expect(page.getByTestId('cancel-reason-NO_LONGER_NEEDED')).toBeChecked();
  await expect(page.getByRole('heading', { name: 'Your refund' })).toHaveCount(0);

  await page.getByTestId('cancel-see-refund').click();
  await page.getByTestId('cancel-confirm').click();
  await expect.poll(() => cancelled).toHaveLength(1);
  expect(cancelled[0]).toEqual({
    id: 'booking-1',
    reason: 'NO_LONGER_NEEDED',
    expectedRefundCents: 2_521,
  });
  expect(await noHorizontalScroll(page)).toBe(true);
});

test('a no-fault reason asks the helper on step 2 instead of cancelling', async ({
  page,
  stubGraphql,
}) => {
  const requested: Variables[] = [];
  await stubGraphql(
    answers(() => booking(), {
      RequestNoFaultCancellation: (variables) => {
        requested.push(variables);
        return { data: { requestNoFaultCancellation: booking() } };
      },
    }),
  );
  await page.goto('/en/bookings/booking-1/cancel');
  await page.getByTestId('cancel-reason-WEATHER').check();
  await page.getByTestId('cancel-see-refund').click();

  const refund = page.getByTestId('cancel-step-refund');
  await expect(refund).toContainText('Your helper has 48 hours to agree');
  await expect(page.getByTestId('cancel-confirm')).toHaveCount(0);
  await page.getByTestId('no-fault-send').click();
  await expect.poll(() => requested).toEqual([{ id: 'booking-1', reason: 'WEATHER' }]);
});

test('a full refund shows the API amount, and the trader withdrawal stays on step 1', async ({
  page,
  stubGraphql,
}) => {
  await stubGraphql(
    answers(() =>
      booking({
        helperIsTrader: true,
        withdrawalAvailable: true,
        withdrawalEndsAt: '2026-11-19T23:00:00Z',
        cancelPreview: {
          ...late,
          case: 'EARLY',
          refundCents: 3_360,
          helperCompensationCents: 0,
          keptFeeCents: 0,
        },
      }),
    ),
  );
  await page.goto('/en/bookings/booking-1/cancel');
  const withdrawal = page.getByTestId('cancel-withdrawal');
  await expect(withdrawal).toContainText('Withdraw from the contract');
  await expect(withdrawal).toContainText('You can withdraw until');
  await expect(withdrawal.getByRole('button', { name: 'Withdraw from contract' })).toBeVisible();

  await page.getByTestId('cancel-reason-OTHER').check();
  await page.getByTestId('cancel-see-refund').click();
  await expect(page.getByTestId('cancel-refund-rows')).toContainText('€33.60');
  await expect(page.getByTestId('cancel-step-refund')).toContainText('Full refund');
});

test('after the start only the withdrawal is offered, with no steps', async ({
  page,
  stubGraphql,
}) => {
  await stubGraphql(
    answers(() =>
      booking({
        helperIsTrader: true,
        withdrawalAvailable: true,
        withdrawalEndsAt: '2026-11-19T23:00:00Z',
        cancelPreview: { ...late, case: null, refusedWith: 'WORK_STARTED' },
      }),
    ),
  );
  await page.goto('/en/bookings/booking-1/cancel');
  await expect(page.getByText('The job has already started')).toBeVisible();
  await expect(page.getByTestId('cancel-withdrawal')).toContainText('The job has started');
  await expect(page.getByTestId('cancel-see-refund')).toHaveCount(0);
  await expect(page.getByTestId('cancel-reason-OTHER')).toHaveCount(0);
});

const tradeBooking = () =>
  booking({
    helperIsTrader: true,
    withdrawalAvailable: true,
    withdrawalEndsAt: '2026-11-19T23:00:00Z',
  });

/** Whether the bar actually sticks to the screen (PinnedFoot's data-pinned and the 40em rule). */
const sticks = (foot: Locator) => foot.evaluate((el) => getComputedStyle(el).position === 'sticky');

async function expectClear(page: Page, foot: Locator, target: Locator, label: string) {
  await target.scrollIntoViewIfNeeded();
  expect(overlaps((await target.boundingBox())!, (await foot.boundingBox())!), label).toBe(false);
}

// The button bar is pinned only while it fits (as BEAV-210's checkout): with 200% text it must
// never cover the reasons, the amounts or the withdrawal information, in any language, on a
// 375px phone (WCAG 2.2 2.4.11 Focus Not Obscured).
for (const height of [812, 667]) {
  test(`with 200% text at 375×${height}, the button bar never covers reasons, amounts or withdrawal`, async ({
    page,
    stubGraphql,
  }, testInfo) => {
    test.skip(testInfo.project.name !== 'mobile', 'a phone-width check');
    await stubGraphql(answers(tradeBooking));
    await page.setViewportSize({ width: 375, height });
    // The browser's default font size, as the text-size setting changes it: 16px → 32px.
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Page.setFontSizes', { fontSizes: { standard: 32, fixed: 26 } });

    for (const locale of ['hr', 'en', 'de', 'fr'] as const) {
      await page.goto(`/${locale}/bookings/booking-1/cancel`);
      const foot = page.getByTestId('cancel-foot');
      await expect(page.getByTestId('cancel-step-reason')).toBeVisible();
      // With this text size the screen is under 40em tall: the bar stays at the end of the step.
      expect(await sticks(foot), locale).toBe(false);
      for (const key of ['NO_LONGER_NEEDED', 'OTHER', 'HOSPITALISATION', 'MUTUAL_AGREEMENT']) {
        const reason = page.getByTestId(`cancel-reason-${key}`);
        await reason.focus();
        await expectClear(page, foot, reason, `${locale} ${key}`);
      }
      await expectClear(page, foot, page.getByTestId('cancel-withdrawal'), `${locale} withdrawal`);
      expect(await noHorizontalScroll(page), locale).toBe(true);

      await page.getByTestId('cancel-reason-OTHER').check();
      await page.getByTestId('cancel-see-refund').click();
      await expect(page.getByTestId('cancel-step-refund')).toBeVisible();
      expect(await sticks(foot), locale).toBe(false);
      await expectClear(page, foot, page.getByTestId('cancel-refund-rows'), `${locale} amounts`);
      expect(await noHorizontalScroll(page), locale).toBe(true);
    }
  });
}

test('at the default text size the button bar is pinned and clear of the focused reason', async ({
  page,
  stubGraphql,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'mobile', 'a phone-width check');
  await stubGraphql(answers(tradeBooking));
  await page.setViewportSize({ width: 375, height: 667 });
  await page.goto('/en/bookings/booking-1/cancel');
  const foot = page.getByTestId('cancel-foot');
  await expect(foot).toHaveAttribute('data-pinned', 'true');
  expect(await sticks(foot)).toBe(true);
  await expect(page.getByTestId('cancel-see-refund')).toBeInViewport();
  for (const key of ['NO_LONGER_NEEDED', 'MUTUAL_AGREEMENT']) {
    const reason = page.getByTestId(`cancel-reason-${key}`);
    await reason.focus();
    expect(overlaps((await reason.boundingBox())!, (await foot.boundingBox())!), key).toBe(false);
  }
});

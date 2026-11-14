import type { Locator, Page } from '@playwright/test';
import { expect, test, type Answers } from './graphql-stub';

/**
 * The day of the job (mockup 18, BEAV-124, aligned in BEAV-212) against a stubbed API, at the
 * mobile (375px) and desktop widths of playwright.config.ts. What each side may do is the
 * server's (`arrival.can*`, backend bookings/arrival.py); every amount is the API's.
 */

function arrival(extra: Record<string, unknown> = {}) {
  return {
    __typename: 'BookingArrival',
    id: 'booking-1',
    onMyWayAt: null,
    etaAt: null,
    lateReportedAt: null,
    lateAcceptedAt: null,
    arrivedAt: null,
    posterConfirmedCameAt: null,
    noShowConfirmedAt: null,
    onTheWayPromptSentAt: null,
    posterAskedAt: null,
    noShowDueAt: null,
    noShowContestBy: null,
    noShowContestedAt: null,
    noShowSettledAt: null,
    noShowAwaitingSupport: false,
    noShowRejected: false,
    noShowRefundCents: null,
    noShowCreditCents: null,
    earlyStartRequestedAt: null,
    earlyStartConfirmedAt: null,
    earlyStartDeclinedAt: null,
    earlyStartPending: false,
    canTap: false,
    canRequestEarlyStart: false,
    canAnswerLate: false,
    canConfirmCame: false,
    canConfirmNoShow: false,
    canContestNoShow: false,
    canAnswerEarlyStart: false,
    earlyStartWithdrawal: false,
    ...extra,
  };
}

function booking(arrivalExtra: Record<string, unknown> = {}) {
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
    arrival: arrival(arrivalExtra),
  };
}

const user = (id: string, firstName: string) => ({
  __typename: 'User',
  id,
  email: `${firstName.toLowerCase()}@example.com`,
  firstName,
  lastName: 'Primjer',
  locale: 'en',
  country: 'HR',
  timeZone: 'Europe/Zagreb',
  phoneVerified: true,
  phoneNumberMasked: null,
});

const helper = user('helper-1', 'Marko');
const poster = user('poster-1', 'Ana');

const answers = (
  viewer: ReturnType<typeof user>,
  bookingNow: () => unknown,
  extra: Answers = {},
): Answers => ({
  BookingArrival: () => ({ data: { booking: bookingNow() } }),
  BookingArrivalTask: () => ({
    data: {
      booking: {
        __typename: 'Booking',
        id: 'booking-1',
        task: { __typename: 'Task', id: 'task-1', title: 'Grocery run for Mama' },
      },
    },
  }),
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

function overlaps(a: { y: number; height: number }, b: { y: number; height: number }) {
  return a.y < b.y + b.height && b.y < a.y + a.height;
}

test('the helper sets off with an ETA, then sends a new arrival time (18a, 18g)', async ({
  page,
  stubGraphql,
}) => {
  let current = booking({ canTap: true });
  const stub = await stubGraphql(
    answers(helper, () => current, {
      MarkOnMyWay: () => {
        current = booking({
          canTap: true,
          onMyWayAt: '2026-11-14T07:40:00Z',
          etaAt: '2026-11-14T08:00:00Z',
        });
        return { data: { markOnMyWay: current } };
      },
      ReportRunningLate: () => {
        current = booking({
          canTap: true,
          onMyWayAt: '2026-11-14T07:40:00Z',
          lateReportedAt: '2026-11-14T07:50:00Z',
          etaAt: '2026-11-14T08:20:00Z',
        });
        return { data: { reportRunningLate: current } };
      },
    }),
  );
  await page.goto('/en/bookings/booking-1/arrival');

  const summary = page.getByTestId('arrival-summary');
  await expect(summary).toContainText('Grocery run for Mama');
  await expect(summary).toContainText('Let the poster know when you set off.');
  const foot = page.getByTestId('arrival-foot');
  await expect(foot.getByTestId('arrival-arrived')).toBeVisible();

  await page.getByText('20 min').click();
  await expect(page.getByTestId('arrival-eta-20')).toBeChecked();
  await foot.getByTestId('arrival-on-my-way').click();
  await expect
    .poll(() => stub.calls.find((c) => c.name === 'MarkOnMyWay')?.variables)
    .toEqual({
      bookingId: 'booking-1',
      etaMinutes: 20,
    });

  // On the way: the same chips now give a new arrival time (18g).
  await expect(summary).toContainText("You're on your way · arriving");
  const send = foot.getByTestId('arrival-send-late');
  await expect(send).toBeDisabled();
  await expect(page.getByTestId('arrival-on-my-way')).toHaveCount(0);
  await page.getByText('30 min').click();
  await send.click();
  await expect
    .poll(() => stub.calls.find((c) => c.name === 'ReportRunningLate')?.variables)
    .toEqual({ bookingId: 'booking-1', etaMinutes: 30 });
  await expect(summary).toContainText("You're running late · arriving");
});

test('the poster answers a late arrival and sees the API refund before saying No (18c-18e)', async ({
  page,
  stubGraphql,
}) => {
  await stubGraphql(
    answers(poster, () =>
      booking({
        lateReportedAt: '2026-11-14T07:50:00Z',
        etaAt: '2026-11-14T08:40:00Z',
        canAnswerLate: true,
        canConfirmCame: true,
        canConfirmNoShow: true,
        noShowRefundCents: 3_360,
        noShowCreditCents: 500,
      }),
    ),
  );
  await page.goto('/en/bookings/booking-1/arrival');

  await expect(page.getByTestId('arrival-late')).toContainText("A late arrival isn't your fault.");
  await expect(page.getByTestId('arrival-late-keep')).toBeVisible();
  await expect(page.getByTestId('arrival-foot')).toHaveCount(0);

  await page.getByTestId('arrival-came-no').click();
  const warning = page.getByTestId('arrival-no-show-warning');
  await expect(warning).toContainText('€33.60');
  await expect(warning).toContainText('€5.00');
  await expect(page.getByTestId('arrival-no-show-confirm')).toBeVisible();
});

// Review R1-1: Running late before setting off must not take On my way away for good.
test('the helper can go back from Running late and still set off', async ({
  page,
  stubGraphql,
}) => {
  const stub = await stubGraphql(
    answers(helper, () => booking({ canTap: true }), {
      MarkOnMyWay: () => ({
        data: {
          markOnMyWay: booking({
            canTap: true,
            onMyWayAt: '2026-11-14T07:40:00Z',
            etaAt: '2026-11-14T08:00:00Z',
          }),
        },
      }),
    }),
  );
  await page.goto('/en/bookings/booking-1/arrival');

  await page.getByTestId('arrival-running-late').click();
  await expect(page.getByTestId('arrival-send-late')).toBeVisible();
  await expect(page.getByTestId('arrival-on-my-way')).toHaveCount(0);

  await page.getByTestId('arrival-late-back').click();
  await expect(page.getByTestId('arrival-send-late')).toHaveCount(0);
  await expect(page.getByTestId('arrival-running-late')).toBeVisible();
  await page.getByTestId('arrival-on-my-way').click();
  await expect
    .poll(() => stub.calls.find((c) => c.name === 'MarkOnMyWay')?.variables)
    .toEqual({
      bookingId: 'booking-1',
      etaMinutes: null,
    });
  expect(stub.calls.some((c) => c.name === 'ReportRunningLate')).toBe(false);
});

test('where a reported no-show stands, for both sides (18e, 18f, 18h)', async ({
  page,
  stubGraphql,
}) => {
  const reported = { noShowContestBy: '2026-11-15T08:20:00Z' };
  await stubGraphql(answers(helper, () => booking({ ...reported, canContestNoShow: true })));
  await page.goto('/en/bookings/booking-1/arrival');
  const card = page.getByTestId('arrival-no-show');
  await expect(card).toContainText("Our team looks at the booking's record");
  await expect(card.getByTestId('arrival-contest')).toBeVisible();
});

for (const [side, viewer, state] of [
  ['helper', helper, { canTap: true }],
  ['poster', poster, { canAnswerLate: true, lateReportedAt: '2026-11-14T07:50:00Z' }],
] as const) {
  test(`the ${side}'s page fits at every width in all four languages`, async ({
    page,
    stubGraphql,
  }) => {
    await stubGraphql(answers(viewer, () => booking(state)));
    for (const locale of ['hr', 'en', 'de', 'fr'] as const) {
      await page.goto(`/${locale}/bookings/booking-1/arrival`);
      await expect(page.getByTestId('arrival-summary')).toContainText('Grocery run for Mama');
      expect(await noHorizontalScroll(page), locale).toBe(true);
    }
  });
}

/** Whether the bar actually sticks to the screen (PinnedFoot's data-pinned and the 40em rule). */
const sticks = (foot: Locator) => foot.evaluate((el) => getComputedStyle(el).position === 'sticky');

async function expectClear(foot: Locator, target: Locator, label: string) {
  await target.scrollIntoViewIfNeeded();
  expect(overlaps((await target.boundingBox())!, (await foot.boundingBox())!), label).toBe(false);
}

// The helper's button bar is pinned only while it fits (BEAV-210, BEAV-211): with 200% text it
// must never cover the booking summary, the chips or the notes, in any language, on a 375px
// phone (WCAG 2.2 2.4.11 Focus Not Obscured).
for (const height of [812, 667]) {
  test(`with 200% text at 375×${height}, the button bar never covers the booking details`, async ({
    page,
    stubGraphql,
  }, testInfo) => {
    test.skip(testInfo.project.name !== 'mobile', 'a phone-width check');
    await stubGraphql(answers(helper, () => booking({ canTap: true })));
    await page.setViewportSize({ width: 375, height });
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Page.setFontSizes', { fontSizes: { standard: 32, fixed: 26 } });

    for (const locale of ['hr', 'en', 'de', 'fr'] as const) {
      await page.goto(`/${locale}/bookings/booking-1/arrival`);
      const foot = page.getByTestId('arrival-foot');
      await expect(page.getByTestId('arrival-summary')).toContainText('Grocery run for Mama');
      expect(await sticks(foot), locale).toBe(false);
      await expectClear(foot, page.getByTestId('arrival-summary'), `${locale} summary`);
      for (const minutes of [10, 60]) {
        const chip = page.getByTestId(`arrival-eta-${minutes}`);
        await chip.focus();
        await expectClear(foot, chip, `${locale} ${minutes} min`);
      }
      expect(await noHorizontalScroll(page), locale).toBe(true);
    }
  });
}

test('at the default text size the button bar is pinned and clear of the focused chip', async ({
  page,
  stubGraphql,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'mobile', 'a phone-width check');
  await stubGraphql(answers(helper, () => booking({ canTap: true })));
  await page.setViewportSize({ width: 375, height: 667 });
  await page.goto('/en/bookings/booking-1/arrival');
  const foot = page.getByTestId('arrival-foot');
  await expect(foot).toHaveAttribute('data-pinned', 'true');
  expect(await sticks(foot)).toBe(true);
  await expect(page.getByTestId('arrival-on-my-way')).toBeInViewport();
  for (const minutes of [10, 60]) {
    const chip = page.getByTestId(`arrival-eta-${minutes}`);
    await chip.focus();
    expect(overlaps((await chip.boundingBox())!, (await foot.boundingBox())!), `${minutes}`).toBe(
      false,
    );
  }
});

for (const [segment, target] of [
  ['release', 'release'],
  ['no-access', 'noAccess'],
] as const) {
  test(`/${segment} stays a placeholder: a card in the centred column, no action`, async ({
    page,
    stubGraphql,
  }) => {
    await stubGraphql(answers(poster, () => booking()));
    await page.goto(`/en/bookings/booking-1/${segment}`);
    const main = page.getByTestId(`link-target-${target}`);
    await expect(main).toContainText('This screen is coming soon.');
    // No money UI and nothing that acts (rule a): only the way home.
    await expect(main.getByRole('button')).toHaveCount(0);
    await expect(main.getByRole('link')).toHaveCount(1);
    await expect(main).not.toContainText('€');
    expect((await main.boundingBox())!.width).toBeLessThanOrEqual(560);
    expect(await noHorizontalScroll(page)).toBe(true);
  });
}

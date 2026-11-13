import { describe, expect, it } from 'vitest';
import { ETA_CHOICES, arrivalRole, arrivalView, minutesUntil } from './arrival';

function booking(extra: Record<string, unknown> = {}) {
  return {
    helperUserId: 'u-helper',
    arrival: {
      id: 'b1',
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
      canTap: true,
      canRequestEarlyStart: false,
      canAnswerLate: false,
      canConfirmCame: false,
      canConfirmNoShow: false,
      canContestNoShow: false,
      canAnswerEarlyStart: false,
      earlyStartWithdrawal: false,
      ...extra,
    },
  } as Parameters<typeof arrivalView>[0];
}

describe('arrivalRole', () => {
  it('never guesses a side without a viewer', () => {
    expect(arrivalRole(booking(), null)).toBe('unknown');
    expect(arrivalRole(booking(), 'u-helper')).toBe('helper');
    expect(arrivalRole(booking(), 'u-poster')).toBe('poster');
    expect(arrivalView(booking(), 'unknown')).toEqual({ kind: 'unknownViewer' });
  });
});

describe('arrivalView', () => {
  it('offers the helper On my way once', () => {
    const fresh = arrivalView(booking(), 'helper');
    expect(fresh).toMatchObject({ kind: 'helper', status: 'none', canMarkOnMyWay: true });
    const going = arrivalView(
      booking({ onMyWayAt: '2026-11-08T09:00:00Z', etaAt: '2026-11-08T09:20:00Z' }),
      'helper',
    );
    expect(going).toMatchObject({
      status: 'onMyWay',
      etaAt: '2026-11-08T09:20:00Z',
      canMarkOnMyWay: false,
    });
  });

  it('shows the latest tap: arrived beats late', () => {
    const view = arrivalView(
      booking({ lateReportedAt: '2026-11-08T09:00:00Z', arrivedAt: '2026-11-08T09:30:00Z' }),
      'poster',
    );
    expect(view).toMatchObject({ kind: 'poster', status: 'arrived' });
  });

  it("offers the poster No with the server's refund and credit", () => {
    const view = arrivalView(
      booking({ canConfirmNoShow: true, noShowRefundCents: 3_360, noShowCreditCents: 500 }),
      'poster',
    );
    expect(view).toMatchObject({ noShowOffer: { refundCents: 3_360, creditCents: 500 } });
    const closed = arrivalView(booking({ noShowRefundCents: 3_360 }), 'poster');
    expect(closed).toMatchObject({ noShowOffer: null });
  });

  it('follows a reported no-show through the 24 hours', () => {
    const reported = { noShowContestBy: '2026-11-09T10:00:00Z', canContestNoShow: true };
    expect(arrivalView(booking(reported), 'helper')).toMatchObject({
      canContestNoShow: true,
      noShow: { contestBy: '2026-11-09T10:00:00Z', contested: false, settled: false },
    });
    const contested = booking({
      ...reported,
      canContestNoShow: false,
      noShowContestedAt: '2026-11-08T12:00:00Z',
      noShowAwaitingSupport: true,
    });
    expect(arrivalView(contested, 'poster')).toMatchObject({
      noShow: { contested: true, awaitingSupport: true },
    });
    const rejected = booking({ ...reported, noShowRejected: true });
    expect(arrivalView(rejected, 'poster')).toMatchObject({ noShow: null, noShowRejected: true });
  });
});

describe('minutesUntil', () => {
  it('rounds up and never goes below 0', () => {
    const now = new Date('2026-11-08T09:00:00Z');
    expect(minutesUntil('2026-11-08T09:00:30Z', now)).toBe(1);
    expect(minutesUntil('2026-11-08T08:00:00Z', now)).toBe(0);
    expect(minutesUntil('nonsense', now)).toBe(0);
  });

  it('offers ETAs the server accepts', () => {
    expect(ETA_CHOICES.every((m) => m >= 1 && m <= 240)).toBe(true);
  });
});

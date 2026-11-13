import { describe, expect, it } from 'vitest';
import {
  HELPER_CANCEL_REASONS,
  NO_FAULT_REASONS,
  POSTER_CANCEL_REASONS,
  cancelRole,
  cancelView,
  hoursLeft,
  noFaultDecision,
} from './cancel';

const ENDS = '2026-11-19T23:00:00+00:00';

function booking(extra: Record<string, unknown> = {}) {
  return {
    status: 'FUNDS_HELD' as const,
    helperUserId: 'u-helper',
    withdrawalAvailable: false,
    withdrawalEndsAt: null,
    cancelPreview: {
      id: 'b1',
      case: 'LATE' as const,
      chargeCents: 3_360,
      refundCents: 2_520,
      helperCompensationCents: 750,
      keptFeeCents: 90,
      creditBackCents: 0,
      refusedWith: null,
    },
    cancellation: null,
    helperCancelPreview: null,
    noFaultRequest: null,
    canRequestNoFault: true,
    canReopenTask: false,
    taskReopened: false,
    ...extra,
  } as Parameters<typeof cancelView>[0];
}

describe('cancelView', () => {
  it('shows the server preview of a late cancel', () => {
    expect(cancelView(booking(), 'poster')).toEqual({
      kind: 'cancel',
      preview: {
        case: 'LATE',
        chargeCents: 3_360,
        refundCents: 2_520,
        helperCompensationCents: 750,
        keptFeeCents: 90,
        creditBackCents: 0,
        full: false,
      },
      withdrawal: null,
      noFault: true,
    });
  });

  it('marks a full refund and offers the withdrawal button', () => {
    const view = cancelView(
      booking({
        withdrawalAvailable: true,
        withdrawalEndsAt: ENDS,
        cancelPreview: {
          ...booking().cancelPreview!,
          case: 'EARLY',
          refundCents: 3_360,
          helperCompensationCents: 0,
          keptFeeCents: 0,
        },
      }),
      'poster',
    );
    expect(view).toMatchObject({
      kind: 'cancel',
      preview: { full: true },
      withdrawal: { endsAt: ENDS },
    });
  });

  it('keeps the withdrawal button when work started', () => {
    const view = cancelView(
      booking({
        withdrawalAvailable: true,
        withdrawalEndsAt: ENDS,
        cancelPreview: { ...booking().cancelPreview!, case: null, refusedWith: 'WORK_STARTED' },
      }),
      'poster',
    );
    expect(view).toEqual({
      kind: 'withdrawOnly',
      refusedWith: 'WORK_STARTED',
      withdrawal: { endsAt: ENDS },
    });
  });

  it('refuses after the helper requested payment', () => {
    const view = cancelView(
      booking({
        status: 'PAYMENT_REQUESTED',
        cancelPreview: {
          ...booking().cancelPreview!,
          case: null,
          refusedWith: 'BOOKING_ACTION_NOT_ALLOWED',
        },
      }),
      'poster',
    );
    expect(view).toEqual({ kind: 'refused', refusedWith: 'BOOKING_ACTION_NOT_ALLOWED' });
  });

  it('shows a cancellation in progress until Stripe confirms it', () => {
    const cancellation = {
      id: 'b1',
      withdrawal: true,
      case: null,
      refundCents: null,
      helperCompensationCents: null,
      keptFeeCents: null,
      awaitingSupport: true,
      confirmed: false,
      requestedAt: ENDS,
      requestedBy: 'POSTER' as const,
      posterCreditCents: 0,
      creditBackCents: 0,
    };
    expect(
      cancelView(
        booking({ status: 'CANCEL_REQUESTED', cancelPreview: null, cancellation }),
        'poster',
      ),
    ).toEqual({
      kind: 'done',
      withdrawal: true,
      awaitingSupport: true,
      refunding: true,
      refundCents: null,
      helperCompensationCents: null,
      byHelper: false,
      posterCreditCents: 0,
      creditBackCents: 0,
      canReopen: false,
      reopened: false,
    });
  });

  it('is unavailable for other statuses', () => {
    expect(cancelView(booking({ status: 'RELEASED', cancelPreview: null }), 'poster')).toEqual({
      kind: 'unavailable',
    });
  });

  it('offers only enum reasons, none of them no-fault', () => {
    expect(POSTER_CANCEL_REASONS).toEqual([
      'NO_LONGER_NEEDED',
      'DIFFERENT_DATE',
      'HELPER_ASKED',
      'OTHER',
    ]);
  });

  it('shows the helper what their cancel means for the poster', () => {
    const view = cancelView(
      booking({
        cancelPreview: null,
        helperCancelPreview: {
          id: 'b1',
          refundCents: 3_360,
          posterCreditCents: 500,
          refusedWith: null,
        },
      }),
      'helper',
    );
    expect(view).toEqual({
      kind: 'helperCancel',
      refundCents: 3_360,
      posterCreditCents: 500,
      noFault: true,
    });
  });

  it('refuses the helper after a payment request', () => {
    const view = cancelView(
      booking({
        cancelPreview: null,
        helperCancelPreview: {
          id: 'b1',
          refundCents: 0,
          posterCreditCents: 0,
          refusedWith: 'BOOKING_ACTION_NOT_ALLOWED',
        },
      }),
      'helper',
    );
    expect(view).toEqual({ kind: 'refused', refusedWith: 'BOOKING_ACTION_NOT_ALLOWED' });
  });

  it('shows an open no-fault request before anything else', () => {
    const noFaultRequest = {
      id: '7',
      reason: null,
      label: 'PERSONAL_EMERGENCY' as const,
      requestedBy: 'POSTER' as const,
      requestedByYou: false,
      requestedAt: ENDS,
      respondBy: '2026-11-21T23:00:00+00:00',
      response: null,
      awaitingSupport: false,
      canRespond: true,
      decision: null,
      open: true,
    };
    expect(
      cancelView(
        booking({ status: 'CANCEL_REQUESTED', cancelPreview: null, noFaultRequest }),
        'helper',
      ),
    ).toEqual({
      kind: 'noFaultPending',
      reason: null,
      label: 'PERSONAL_EMERGENCY',
      requestedByYou: false,
      canRespond: true,
      awaitingSupport: false,
      respondBy: '2026-11-21T23:00:00+00:00',
    });
  });

  it('offers the poster, not the helper, to re-open the task after a helper cancel', () => {
    const cancellation = {
      id: 'b1',
      withdrawal: false,
      case: 'HELPER_CANCEL' as const,
      refundCents: 3_360,
      helperCompensationCents: 0,
      keptFeeCents: 0,
      awaitingSupport: false,
      confirmed: true,
      requestedAt: ENDS,
      requestedBy: 'HELPER' as const,
      posterCreditCents: 500,
      creditBackCents: 360,
    };
    const done = booking({
      status: 'REFUNDED',
      cancelPreview: null,
      cancellation,
      canReopenTask: true,
    });
    expect(cancelView(done, 'poster')).toMatchObject({
      kind: 'done',
      byHelper: true,
      posterCreditCents: 500,
      creditBackCents: 360,
      canReopen: true,
    });
    expect(cancelView(done, 'helper')).toMatchObject({ kind: 'done', canReopen: false });
  });

  it('tells the sides apart by the helper user ID', () => {
    expect(cancelRole(booking(), 'u-helper')).toBe('helper');
    expect(cancelRole(booking(), 'u-poster')).toBe('poster');
    // Unknown until the viewer loads: never guess the poster (R1-11).
    expect(cancelRole(booking(), null)).toBe('unknown');
    expect(cancelView(booking(), 'unknown')).toEqual({ kind: 'unknownViewer' });
  });

  it('counts the hours left up and never below zero', () => {
    const now = new Date('2026-11-20T00:00:00Z');
    expect(hoursLeft('2026-11-21T23:00:00Z', now)).toBe(47);
    expect(hoursLeft('2026-11-20T00:30:00Z', now)).toBe(1);
    expect(hoursLeft('2026-11-19T00:00:00Z', now)).toBe(0);
  });

  it('lists the helper and no-fault reasons as enum keys', () => {
    expect(HELPER_CANCEL_REASONS).toEqual(['ILL', 'DOUBLE_BOOKED', 'OTHER']);
    expect(NO_FAULT_REASONS).toContain('WEATHER');
    expect(NO_FAULT_REASONS).toContain('HOSPITALISATION');
  });

  it('gives support’s decision only once the request is closed', () => {
    const request = {
      id: '7',
      reason: 'WEATHER' as const,
      requestedBy: 'POSTER' as const,
      requestedByYou: true,
      requestedAt: ENDS,
      respondBy: ENDS,
      response: 'DISAGREED' as const,
      awaitingSupport: true,
      canRespond: false,
      decision: null,
      open: true,
    };
    expect(noFaultDecision(booking({ noFaultRequest: request }))).toBeNull();
    expect(
      noFaultDecision(
        booking({ noFaultRequest: { ...request, open: false, decision: 'REJECTED' as const } }),
      ),
    ).toBe('REJECTED');
    expect(noFaultDecision(booking())).toBeNull();
  });
});

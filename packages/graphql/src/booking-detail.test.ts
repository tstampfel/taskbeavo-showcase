import { describe, expect, it } from 'vitest';
import { bookingDetailView, bookingRole, subscribeToBookingDetail } from './booking-detail';
import { BookingDetailUpdatedDocument, type BookingStatus } from './generated/graphql';

const NOW = new Date('2026-11-09T10:00:00Z');
const LATER = '2026-11-09T10:20:00Z';
const EARLIER = '2026-11-09T09:40:00Z';

type Booking = Parameters<typeof bookingDetailView>[0];

function booking(extra: Partial<Booking> = {}, task: Partial<Booking['task']> = {}): Booking {
  return {
    helperUserId: 'u-helper',
    status: 'FUNDS_HELD',
    checkoutExpiresAt: null,
    lastPaymentAttempt: null,
    checkoutFailureReason: null,
    task: {
      id: 't1',
      title: 'Grocery run for Mama',
      category: null,
      serviceAreaName: 'Dubrovnik',
      address: 'Ulica Ante Starčevića 24, Gruž',
      accessDetails: 'Gate 4417',
      onSiteContact: { id: 'c1', name: 'Mama Kata', area: 'Gruž', language: 'HR', phone: null },
      ...task,
    },
    helperBusiness: null,
    cancelPreview: null,
    helperCancelPreview: null,
    withdrawalAvailable: false,
    canRequestNoFault: false,
    noFaultRequest: null,
    cancellation: null,
    arrival: { id: 'b1', noShowContestBy: null },
    ...extra,
  };
}

const hidden = { address: null, accessDetails: null, onSiteContact: null };
const view = (b: Booking, role: 'poster' | 'helper' = 'poster') => {
  const v = bookingDetailView(b, role, NOW);
  if (v.kind === 'unknownViewer') throw new Error('expected a side');
  return v;
};

describe('bookingRole', () => {
  it('is the helper for the booked helper, the poster for anyone else the server let in', () => {
    expect(bookingRole(booking(), 'u-helper')).toBe('helper');
    expect(bookingRole(booking(), 'u-poster')).toBe('poster');
    expect(bookingRole(booking(), null)).toBe('unknown');
  });

  it('never shows a side to an unknown viewer', () => {
    expect(bookingDetailView(booking(), 'unknown', NOW)).toEqual({ kind: 'unknownViewer' });
  });
});

describe('the stepper', () => {
  const states = (status: BookingStatus) =>
    view(booking({ status })).steps?.map((s) => `${s.step}:${s.state}`);

  it('follows the happy path of mockup 05', () => {
    expect(states('AWAITING_PAYMENT')).toEqual([
      'accepted:current',
      'held:todo',
      'requested:todo',
      'released:todo',
    ]);
    expect(states('FUNDS_HELD')).toEqual([
      'accepted:done',
      'held:current',
      'requested:todo',
      'released:todo',
    ]);
    expect(states('PAYMENT_REQUESTED')?.[2]).toBe('requested:current');
    expect(states('PAID_OUT')?.[3]).toBe('released:current');
    expect(states('CLOSED')).toEqual([
      'accepted:done',
      'held:done',
      'requested:done',
      'released:done',
    ]);
  });

  it.each<BookingStatus>([
    'PAYMENT_FAILED',
    'CANCEL_REQUESTED',
    'NO_ACCESS_REPORTED',
    'INCIDENT_PAUSED',
    'DISPUTED',
    'REFUNDED',
    'PARTIALLY_SETTLED',
  ])('is left out off the happy path (%s)', (status) => {
    expect(view(booking({ status })).steps).toBeNull();
  });
});

describe('the place (hard rule e: the server decides, the screen shows what it got)', () => {
  it('shows the exact place and access details the server sent', () => {
    expect(view(booking(), 'helper').place).toEqual({
      kind: 'exact',
      address: 'Ulica Ante Starčevića 24, Gruž',
      accessDetails: 'Gate 4417',
      contact: { id: 'c1', name: 'Mama Kata', area: 'Gruž', language: 'HR', phone: null },
      onlyWhileBooked: true,
    });
    expect(view(booking(), 'poster').place).toMatchObject({ onlyWhileBooked: false });
  });

  it('falls back to the area once the server stops sending the address', () => {
    expect(view(booking({ status: 'CLOSED' }, hidden), 'helper').place).toEqual({
      kind: 'area',
      area: 'Dubrovnik',
      hint: 'ended',
    });
  });

  it('tells a helper waiting for the payment when the address comes', () => {
    expect(view(booking({ status: 'AWAITING_PAYMENT' }, hidden), 'helper').place).toEqual({
      kind: 'area',
      area: 'Dubrovnik',
      hint: 'afterPayment',
    });
  });

  it('waits for the server before showing the helper an exact place it had in memory', () => {
    const stale = bookingDetailView(booking(), 'helper', NOW, { fresh: false });
    expect(stale).toMatchObject({ place: { kind: 'area', area: 'Dubrovnik', hint: null } });
    // The poster's own task needs no wait.
    const poster = bookingDetailView(booking(), 'poster', NOW, { fresh: false });
    expect(poster).toMatchObject({ place: { kind: 'exact' } });
  });

  it('never shows access details or a contact without the address', () => {
    const leaky = booking({}, { address: null });
    expect(view(leaky, 'helper').place.kind).toBe('area');
  });
});

describe('the checkout state (BEAV-110 live events)', () => {
  const awaiting = (extra: Partial<Booking> = {}) =>
    booking({ status: 'AWAITING_PAYMENT', checkoutExpiresAt: LATER, ...extra }, hidden);

  it('asks the poster to pay before the checkout ends', () => {
    expect(view(awaiting()).checkout).toEqual({ kind: 'pay', expiresAt: LATER, decline: null });
    expect(view(awaiting()).links.checkout).toBe(true);
  });

  it('shows the decline of the last attempt', () => {
    const declined = awaiting({
      lastPaymentAttempt: {
        id: 'a1',
        kind: 'DECLINED',
        declineCode: 'insufficient_funds',
        at: EARLIER,
      },
    });
    expect(view(declined).checkout).toEqual({
      kind: 'pay',
      expiresAt: LATER,
      decline: 'insufficient_funds',
    });
  });

  it('says the bank asked to confirm', () => {
    const asked = awaiting({
      lastPaymentAttempt: {
        id: 'a1',
        kind: 'AUTHENTICATION_STARTED',
        declineCode: null,
        at: EARLIER,
      },
    });
    expect(view(asked).checkout).toEqual({ kind: 'authenticating' });
    expect(view(asked).links.checkout).toBe(true);
  });

  it('shows an ended checkout as failed before the expiry job runs', () => {
    expect(view(awaiting({ checkoutExpiresAt: EARLIER })).checkout).toEqual({
      kind: 'failed',
      reason: 'expired',
    });
    expect(view(awaiting({ checkoutExpiresAt: EARLIER })).links.checkout).toBe(false);
  });

  it('says why a failed checkout ended', () => {
    const failed = (reason: Booking['checkoutFailureReason']) =>
      view(booking({ status: 'PAYMENT_FAILED', checkoutFailureReason: reason }, hidden)).checkout;
    expect(failed('OFFER_WITHDRAWN')).toEqual({ kind: 'failed', reason: 'withdrawn' });
    expect(failed('PAYMENT_NOT_STARTED')).toEqual({ kind: 'failed', reason: 'notStarted' });
    expect(failed('CHECKOUT_EXPIRED')).toEqual({ kind: 'failed', reason: 'expired' });
  });

  it('tells the helper only that the poster is paying', () => {
    expect(view(awaiting(), 'helper').checkout).toEqual({ kind: 'waiting' });
    expect(view(awaiting(), 'helper').links.checkout).toBe(false);
  });

  it('has nothing to say once the money is held', () => {
    expect(view(booking()).checkout).toBeNull();
  });
});

describe('the links into the other booking screens', () => {
  it('opens the day of the job while the money is held and while a no-show is open', () => {
    expect(view(booking()).links.arrival).toBe(true);
    expect(view(booking({ status: 'PAYMENT_REQUESTED' })).links.arrival).toBe(false);
    const reported = booking({
      status: 'CANCEL_REQUESTED',
      arrival: { id: 'b1', noShowContestBy: LATER },
    });
    expect(view(reported, 'helper').links.arrival).toBe(true);
  });

  it('offers cancel to the poster only when the server has something to show there', () => {
    expect(view(booking()).links.cancel).toBe(false);
    expect(view(booking({ cancelPreview: { id: 'b1' } })).links.cancel).toBe(true);
    expect(view(booking({ withdrawalAvailable: true })).links.cancel).toBe(true);
    expect(view(booking({ cancellation: { id: 'b1' } })).links.cancel).toBe(true);
    // The helper's preview is never the poster's way in.
    expect(view(booking({ helperCancelPreview: { id: 'b1' } })).links.cancel).toBe(false);
  });

  it("offers cancel to the helper from the helper's own flags", () => {
    expect(view(booking({ helperCancelPreview: { id: 'b1' } }), 'helper').links.cancel).toBe(true);
    expect(view(booking({ cancelPreview: { id: 'b1' } }), 'helper').links.cancel).toBe(false);
    expect(
      view(booking({ noFaultRequest: { id: 'n1', canRespond: true } }), 'helper').links.cancel,
    ).toBe(true);
    expect(view(booking({ canRequestNoFault: true }), 'helper').links.cancel).toBe(true);
  });

  it('sends the poster to release and to the no-access choice', () => {
    expect(view(booking({ status: 'PAYMENT_REQUESTED' })).links.release).toBe(true);
    expect(view(booking({ status: 'PAYMENT_REQUESTED' }), 'helper').links.release).toBe(false);
    expect(view(booking({ status: 'NO_ACCESS_REPORTED' })).links.noAccess).toBe(true);
    expect(view(booking({ status: 'NO_ACCESS_REPORTED' }), 'helper').links.noAccess).toBe(false);
  });
});

describe('who the poster contracts with (BEAV-192)', () => {
  it("gives the trader's confirmed name and OIB in the offer's shape, never an address", () => {
    const trader = booking({
      helperBusiness: {
        id: 'b1',
        businessName: 'Vrtlarski obrt Horvat',
        businessOib: '00000000124',
      },
    });
    expect(view(trader).trader).toEqual({
      helperIsTrader: true,
      helperBusinessName: 'Vrtlarski obrt Horvat',
      helperBusinessOib: '00000000124',
      helperBusinessAddress: null,
    });
  });

  it('says private individual otherwise, and nothing to the helper', () => {
    expect(view(booking()).trader).toEqual({
      helperIsTrader: false,
      helperBusinessName: null,
      helperBusinessOib: null,
      helperBusinessAddress: null,
    });
    expect(view(booking(), 'helper').trader).toBeNull();
  });

  it('notes the emailed confirmation once the money is held', () => {
    expect(view(booking()).confirmationEmailed).toBe(true);
    expect(view(booking({ status: 'AWAITING_PAYMENT' })).confirmationEmailed).toBe(false);
    expect(view(booking({ status: 'PAYMENT_FAILED' })).confirmationEmailed).toBe(false);
    expect(view(booking(), 'helper').confirmationEmailed).toBe(false);
  });
});

describe('subscribeToBookingDetail', () => {
  it('follows the detail fields, so a closing event clears the address', () => {
    const calls: unknown[] = [];
    const unsubscribe = () => undefined;
    const result = subscribeToBookingDetail((options) => {
      calls.push(options);
      return unsubscribe;
    }, 'b1');
    expect(result).toBe(unsubscribe);
    expect(calls).toEqual([{ document: BookingDetailUpdatedDocument, variables: { id: 'b1' } }]);
  });
});

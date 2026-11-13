import { describe, expect, it, vi } from 'vitest';
import {
  OfferExpiringDocument,
  OfferReceivedDocument,
  OfferUpdatedDocument,
  OfferWithdrawnDocument,
  type TaskOffersQuery,
} from './generated/graphql';
import {
  arrangeOffers,
  canAcceptOffer,
  msUntilNextExpiry,
  subscribeToTaskOffers,
  withNewOffer,
} from './offers';

type Offer = TaskOffersQuery['taskOffers'][number];

function offer(id: string, extra: Partial<Offer> = {}): Offer {
  return {
    id,
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
    withdrawnReason: null,
    expiresAt: '2026-11-10T10:00:00Z',
    isMine: false,
    createdAt: '2026-11-03T10:00:00Z',
    updatedAt: '2026-11-03T10:00:00Z',
    ...extra,
  };
}

const event = (o: Offer | null) => ({ subscriptionData: { data: o && { offerReceived: o } } });

describe('withNewOffer', () => {
  it('puts a new offer first', () => {
    const previous = { taskOffers: [offer('a')] };

    const next = withNewOffer(previous, event(offer('b')));

    expect(next.taskOffers.map((o) => o.id)).toEqual(['b', 'a']);
  });

  it('ignores an offer already in the list, so a redelivered event adds nothing', () => {
    const previous = { taskOffers: [offer('b'), offer('a')] };

    expect(withNewOffer(previous, event(offer('a')))).toBe(previous);
  });

  it('keeps the list when the event has no data', () => {
    const previous = { taskOffers: [offer('a')] };

    expect(withNewOffer(previous, event(null))).toBe(previous);
    expect(withNewOffer(previous, { subscriptionData: {} })).toBe(previous);
  });
});

const NOW = new Date('2026-11-05T10:00:00Z');
const ids = (list: readonly Offer[]) => list.map((o) => o.id);

describe('arrangeOffers', () => {
  // The API's order: newest first.
  const list = [
    offer('withdrawn', { status: 'WITHDRAWN', priceCents: 2_000 }),
    offer('cheap', { priceCents: 2_500 }),
    offer('expired', { status: 'EXPIRED', priceCents: 1_500 }),
    offer('changed', { status: 'NEEDS_UPDATE', priceCents: 4_000 }),
    offer('dear', { priceCents: 3_800 }),
  ];

  it('keeps open offers newest first and puts withdrawn and expired ones after them', () => {
    const { open, closed } = arrangeOffers(list, 'newest', NOW);

    expect(ids(open)).toEqual(['cheap', 'changed', 'dear']);
    expect(ids(closed)).toEqual(['withdrawn', 'expired']);
  });

  it('sorts open offers by price, lowest first, leaving closed ones at the end', () => {
    const { open, closed } = arrangeOffers(list, 'lowestPrice', NOW);

    expect(ids(open)).toEqual(['cheap', 'dear', 'changed']);
    expect(ids(closed)).toEqual(['withdrawn', 'expired']);
  });

  it('counts an offer past its expiry as closed before the server marks it EXPIRED', () => {
    const late = offer('late', { expiresAt: '2026-11-05T09:59:59Z' });

    expect(ids(arrangeOffers([late], 'newest', NOW).closed)).toEqual(['late']);
  });

  it('returns new arrays and leaves the cached list as it was', () => {
    const frozen = Object.freeze([...list]);

    arrangeOffers(frozen, 'lowestPrice', NOW);

    expect(ids(frozen)).toEqual(ids(list));
  });
});

describe('canAcceptOffer', () => {
  it('accepts a sent offer that has not expired', () => {
    expect(canAcceptOffer(offer('a'), NOW)).toBe(true);
  });

  it('refuses one the helper must confirm first, and closed or late ones', () => {
    expect(canAcceptOffer(offer('a', { status: 'NEEDS_UPDATE' }), NOW)).toBe(false);
    expect(canAcceptOffer(offer('a', { status: 'WITHDRAWN' }), NOW)).toBe(false);
    expect(canAcceptOffer(offer('a', { status: 'EXPIRED' }), NOW)).toBe(false);
    expect(canAcceptOffer(offer('a', { expiresAt: '2026-11-05T10:00:00Z' }), NOW)).toBe(false);
  });
});

describe('subscribeToTaskOffers', () => {
  it('subscribes to all four offer events for the task and unsubscribes from all', () => {
    const unsubscribers = [vi.fn(), vi.fn(), vi.fn(), vi.fn()];
    const subscribeToMore = vi.fn(() => unsubscribers.shift() ?? vi.fn());
    const all = [...unsubscribers];

    const stop = subscribeToTaskOffers(subscribeToMore, 'task-1');

    const calls = subscribeToMore.mock.calls as unknown as [
      { document: unknown; variables: unknown; updateQuery?: unknown },
    ][];
    expect(calls.map(([options]) => options.document)).toEqual([
      OfferReceivedDocument,
      OfferWithdrawnDocument,
      OfferExpiringDocument,
      OfferUpdatedDocument,
    ]);
    expect(
      calls.every(([options]) => (options.variables as { taskId: string }).taskId === 'task-1'),
    ).toBe(true);
    // Only a new offer changes the list; the others update cached offers by ID.
    expect(calls.map(([options]) => typeof options.updateQuery)).toEqual([
      'function',
      'undefined',
      'undefined',
      'undefined',
    ]);
    stop();
    for (const unsubscribe of all) expect(unsubscribe).toHaveBeenCalledOnce();
  });

  it('adds a received offer to a complete list and leaves a partial one alone', () => {
    const subscribeToMore = vi.fn((_options: unknown) => vi.fn());
    subscribeToTaskOffers(subscribeToMore, 'task-1');
    const { updateQuery } = subscribeToMore.mock.calls[0]![0] as {
      updateQuery: (prev: unknown, options: unknown) => TaskOffersQuery | undefined;
    };
    const previousData = { taskOffers: [offer('a')] };
    const subscriptionData = { data: { offerReceived: offer('b') } };

    const next = updateQuery(previousData, { complete: true, previousData, subscriptionData });

    expect(next && ids(next.taskOffers)).toEqual(['b', 'a']);
    expect(
      updateQuery({}, { complete: false, previousData: {}, subscriptionData }),
    ).toBeUndefined();
  });
});

describe('msUntilNextExpiry', () => {
  it('is the time until the earliest open offer expires', () => {
    const list = [
      offer('a', { expiresAt: '2026-11-05T12:00:00Z' }),
      offer('b', { expiresAt: '2026-11-05T10:30:00Z' }),
      offer('gone', { status: 'WITHDRAWN', expiresAt: '2026-11-05T10:01:00Z' }),
    ];

    expect(msUntilNextExpiry(list, NOW)).toBe(30 * 60 * 1000);
  });

  it('is null when no open offer is left to expire', () => {
    const list = [
      offer('late', { expiresAt: '2026-11-05T09:00:00Z' }),
      offer('gone', { status: 'EXPIRED' }),
    ];

    expect(msUntilNextExpiry(list, NOW)).toBeNull();
    expect(msUntilNextExpiry([], NOW)).toBeNull();
  });
});

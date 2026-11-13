import type { SubscribeToMoreFunction } from '@apollo/client';
import {
  OfferExpiringDocument,
  OfferReceivedDocument,
  OfferUpdatedDocument,
  OfferWithdrawnDocument,
  type OfferReceivedSubscription,
  type TaskOffersQuery,
  type TaskOffersQueryVariables,
} from './generated/graphql';

type TaskOffer = TaskOffersQuery['taskOffers'][number];

/**
 * `updateQuery` for `subscribeToMore` on TaskOffers with the OfferReceived subscription:
 * the new offer goes first (the list is newest first). An offer already in the list, from a
 * redelivered event or a refetch that raced the event, is left where it is; the normalised
 * cache has already merged its fields.
 */
export function withNewOffer(
  previous: TaskOffersQuery,
  { subscriptionData }: { subscriptionData: { data?: OfferReceivedSubscription | null } },
): TaskOffersQuery {
  const offer = subscriptionData.data?.offerReceived;
  if (!offer || previous.taskOffers.some((existing) => existing.id === offer.id)) {
    return previous;
  }
  return { ...previous, taskOffers: [offer, ...previous.taskOffers] };
}

/**
 * Keeps a poster's TaskOffers list live: a new offer is prepended (`withNewOffer`); a
 * withdrawal, an expiry reminder and an edit or renewal carry an offer already in the list,
 * which the normalised cache updates by ID. Returns one function that unsubscribes from all
 * four. Missed events come back with the refetch on reconnect and on return to the foreground.
 */
export function subscribeToTaskOffers(
  subscribeToMore: SubscribeToMoreFunction<TaskOffersQuery, TaskOffersQueryVariables>,
  taskId: string,
): () => void {
  const variables = { taskId };
  const stops = [
    subscribeToMore({
      document: OfferReceivedDocument,
      variables,
      updateQuery: (_unsafe, options) =>
        options.complete ? withNewOffer(options.previousData, options) : undefined,
    }),
    subscribeToMore({ document: OfferWithdrawnDocument, variables }),
    subscribeToMore({ document: OfferExpiringDocument, variables }),
    subscribeToMore({ document: OfferUpdatedDocument, variables }),
  ];
  return () => stops.forEach((stop) => stop());
}

/** How the poster orders the open offers. Ranking by rating comes with ratings (BEAV-144). */
export type OfferSort = 'newest' | 'lowestPrice';

export const OFFER_SORTS: readonly OfferSort[] = ['newest', 'lowestPrice'];

// DateTime is an ISO string on the wire; codegen types custom scalars as `unknown`.
const notExpired = (offer: TaskOffer, now: Date) =>
  Date.parse(String(offer.expiresAt)) > now.getTime();

const isOpen = (offer: TaskOffer, now: Date) =>
  (offer.status === 'SENT' || offer.status === 'NEEDS_UPDATE') && notExpired(offer, now);

/**
 * The poster's offers split for the comparison screens: `open` ones in the chosen order, and
 * `closed` ones (withdrawn, expired, or past their expiry before the Beat task marked them)
 * newest first, shown greyed after them as in mockup 4a. The input keeps the API's order
 * (newest first) and is never changed.
 */
export function arrangeOffers<T extends TaskOffer>(
  offers: readonly T[],
  sort: OfferSort,
  now: Date,
): { open: T[]; closed: T[] } {
  const open = offers.filter((offer) => isOpen(offer, now));
  const closed = offers.filter((offer) => !isOpen(offer, now));
  if (sort === 'lowestPrice') open.sort((a, b) => a.priceCents - b.priceCents);
  return { open, closed };
}

/**
 * Whether the poster may accept the offer: SENT and not expired. A NEEDS_UPDATE offer waits
 * for the helper to confirm it after the task changed; the server checks again on accept.
 */
export function canAcceptOffer(offer: TaskOffer, now: Date): boolean {
  return offer.status === 'SENT' && notExpired(offer, now);
}

/**
 * Milliseconds until the earliest open offer expires, or null if none will. Screens re-render
 * then, so an offer that expires while the list is open moves to the closed ones and its
 * Accept turns off without waiting for the Beat task or a refetch.
 */
export function msUntilNextExpiry(offers: readonly TaskOffer[], now: Date): number | null {
  const times = offers
    .filter((offer) => isOpen(offer, now))
    .map((offer) => Date.parse(String(offer.expiresAt)) - now.getTime());
  return times.length ? Math.min(...times) : null;
}

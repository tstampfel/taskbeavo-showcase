import type { SubscribeToMoreFunction } from '@apollo/client';
import { checkoutView, type DeclineKey } from './checkout';
import {
  BookingDetailUpdatedDocument,
  type BookingDetailFieldsFragment,
  type BookingDetailQuery,
  type BookingDetailQueryVariables,
  type BookingFieldsFragment,
  type BookingStatus,
  type OfferFieldsFragment,
} from './generated/graphql';

/**
 * What the booking detail screen shows (BEAV-115, mockup 05): the hub both sides land on,
 * linking to the screens that act (checkout, the day of the job, cancel, no-access, release).
 * It decides nothing itself: every flag comes from the server's booking.
 *
 * - `steps`: the stepper of mockup 05 on the happy path; null off it (cancelled, disputed…),
 *   where the status line says where the booking stands.
 * - `place`: the exact address, access details and on-site contact when the server sent
 *   them, otherwise the approximate area. Only the server decides who sees the exact place
 *   (hard rule e, `bookings.queries.active`); a live event that ends the booking arrives
 *   without them and the screen falls back to the area. The helper's exact place shows only
 *   once the server has answered (`fresh`): a copy left in memory by an earlier screen may
 *   predate the end of the booking.
 * - `checkout`: while AWAITING_PAYMENT, or after a failed checkout, the state the BEAV-110
 *   live events (a declined card, a 3D Secure start) and the expiry leave it in. Payment is
 *   never decided here (hard rule a).
 * - `trader`: for the poster, who they contract with (BEAV-192): the business name and OIB,
 *   in the offer's shape so the offer's trader identity component shows it, never the
 *   registered address, which stays on the offer and the checkout (hard rule e); null for
 *   the helper.
 * - `confirmationEmailed`: the poster has the FUNDS_HELD contract confirmation by email.
 */
export type BookingDetailView = ({ kind: 'detail' } & BookingDetail) | { kind: 'unknownViewer' };

export interface BookingDetail {
  role: 'poster' | 'helper';
  status: BookingStatus;
  steps: { step: BookingStep; state: StepState }[] | null;
  place: BookingPlace;
  checkout: DetailCheckout | null;
  trader: TraderFields | null;
  confirmationEmailed: boolean;
  links: BookingLinks;
}

export type BookingRole = 'poster' | 'helper' | 'unknown';

export const BOOKING_STEPS = ['accepted', 'held', 'requested', 'released'] as const;
export type BookingStep = (typeof BOOKING_STEPS)[number];
export type StepState = 'done' | 'current' | 'todo';

type Contact = NonNullable<BookingDetailFieldsFragment['task']['onSiteContact']>;

export type BookingPlace =
  | {
      kind: 'exact';
      address: string;
      accessDetails: string | null;
      contact: Contact | null;
      /** Say "Shown to you only while booked" (the helper's view). */
      onlyWhileBooked: boolean;
    }
  | {
      kind: 'area';
      area: string | null;
      /** For the helper: the address comes once the payment is confirmed, or the booking
       * has ended and with it the access to the address. */
      hint: 'afterPayment' | 'ended' | null;
    };

export type DetailCheckout =
  | { kind: 'pay'; expiresAt: string | null; decline: DeclineKey | null }
  | { kind: 'authenticating' }
  | { kind: 'waiting' }
  | { kind: 'failed'; reason: 'expired' | 'withdrawn' | 'notStarted' };

export interface BookingLinks {
  checkout: boolean;
  arrival: boolean;
  cancel: boolean;
  noAccess: boolean;
  release: boolean;
}

export type TraderFields = Pick<
  OfferFieldsFragment,
  'helperIsTrader' | 'helperBusinessName' | 'helperBusinessOib' | 'helperBusinessAddress'
>;

type DetailBooking = Pick<
  BookingFieldsFragment,
  'helperUserId' | 'status' | 'checkoutExpiresAt' | 'lastPaymentAttempt' | 'checkoutFailureReason'
> &
  Omit<BookingDetailFieldsFragment, 'id'>;

export function bookingRole(
  booking: Pick<BookingFieldsFragment, 'helperUserId'>,
  viewerId: string | null,
): BookingRole {
  if (viewerId === null) return 'unknown';
  return booking.helperUserId === viewerId ? 'helper' : 'poster';
}

/** The stepper's position: the current step's index, BOOKING_STEPS.length when all done. */
const STEP_AT: Partial<Record<BookingStatus, number>> = {
  AWAITING_PAYMENT: 0,
  FUNDS_HELD: 1,
  PAYMENT_REQUESTED: 2,
  RELEASED: 3,
  PAID_OUT: 3,
  CLOSED: BOOKING_STEPS.length,
};

const HELD_OR_LATER = (status: BookingStatus) =>
  status !== 'AWAITING_PAYMENT' && status !== 'PAYMENT_FAILED';

export function bookingDetailView(
  booking: DetailBooking,
  role: BookingRole,
  now: Date,
  { fresh = true }: { fresh?: boolean } = {},
): BookingDetailView {
  if (role === 'unknown') return { kind: 'unknownViewer' };
  const poster = role === 'poster';
  const { status } = booking;
  const checkout = detailCheckout(booking, poster, now);
  const at = STEP_AT[status];
  return {
    kind: 'detail',
    role,
    status,
    steps:
      at === undefined
        ? null
        : BOOKING_STEPS.map((step, i) => ({
            step,
            state: i < at ? 'done' : i === at ? 'current' : 'todo',
          })),
    place: place(booking, poster, fresh),
    checkout,
    trader: poster ? traderFields(booking) : null,
    confirmationEmailed: poster && HELD_OR_LATER(status),
    links: {
      checkout: poster && (checkout?.kind === 'pay' || checkout?.kind === 'authenticating'),
      arrival: status === 'FUNDS_HELD' || booking.arrival.noShowContestBy != null,
      cancel:
        booking.cancellation != null ||
        booking.canRequestNoFault ||
        booking.noFaultRequest?.canRespond === true ||
        (poster
          ? booking.cancelPreview != null || booking.withdrawalAvailable
          : booking.helperCancelPreview != null),
      noAccess: poster && status === 'NO_ACCESS_REPORTED',
      release: poster && status === 'PAYMENT_REQUESTED',
    },
  };
}

function place(booking: DetailBooking, poster: boolean, fresh: boolean): BookingPlace {
  const { address, accessDetails, onSiteContact, serviceAreaName } = booking.task;
  if (address && !poster && !fresh) return { kind: 'area', area: serviceAreaName, hint: null };
  if (address) {
    return {
      kind: 'exact',
      address,
      accessDetails,
      contact: onSiteContact,
      onlyWhileBooked: !poster,
    };
  }
  return {
    kind: 'area',
    area: serviceAreaName,
    hint: poster ? null : booking.status === 'AWAITING_PAYMENT' ? 'afterPayment' : 'ended',
  };
}

function detailCheckout(booking: DetailBooking, poster: boolean, now: Date): DetailCheckout | null {
  const { status } = booking;
  if (status !== 'AWAITING_PAYMENT' && status !== 'PAYMENT_FAILED') return null;
  const view = checkoutView(booking, { submitted: null }, now);
  switch (view.kind) {
    case 'expired':
      return { kind: 'failed', reason: 'expired' };
    case 'withdrawn':
      return { kind: 'failed', reason: 'withdrawn' };
    case 'notStarted':
      return { kind: 'failed', reason: 'notStarted' };
    case 'pay':
      // The card attempts are the poster's (the helper never gets them).
      if (!poster) return { kind: 'waiting' };
      if (booking.lastPaymentAttempt?.kind === 'AUTHENTICATION_STARTED') {
        return { kind: 'authenticating' };
      }
      return {
        kind: 'pay',
        expiresAt: typeof booking.checkoutExpiresAt === 'string' ? booking.checkoutExpiresAt : null,
        decline: view.decline as DeclineKey | null,
      };
    default:
      return poster ? null : { kind: 'waiting' };
  }
}

function traderFields(booking: DetailBooking): TraderFields {
  const business = booking.helperBusiness;
  return {
    helperIsTrader: business != null,
    helperBusinessName: business?.businessName ?? null,
    helperBusinessOib: business?.businessOib ?? null,
    helperBusinessAddress: null,
  };
}

/**
 * Keeps the detail screen live: every status change, and for the poster each card attempt,
 * arrives as the whole booking with its task as the viewer may now see it, which the
 * normalised cache merges by ID. Missed events come back with the refetch on reconnect and
 * on return to the foreground.
 */
export function subscribeToBookingDetail(
  subscribeToMore: SubscribeToMoreFunction<BookingDetailQuery, BookingDetailQueryVariables>,
  id: string,
): () => void {
  return subscribeToMore({ document: BookingDetailUpdatedDocument, variables: { id } });
}

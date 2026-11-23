import type { BookingRole } from './booking-detail';
import type { BookingStatus, ReleaseFieldsFragment } from './generated/graphql';

/**
 * The Release payment screens (BEAV-118, mockup 07d–7f), shared by web and mobile, from the
 * BEAV-117 API. They decide nothing about money: `canReleasePayment`, the proposal's amounts
 * and the tip's status are the server's, and every check here is one the server repeats
 * (backend/bookings/release.py, bookings/tips.py) to save a round trip.
 *
 * Release then tip: `releasePayment` releases the task price; a tip is a separate payment
 * the poster confirms in Stripe's sheet afterwards (`addTip`), with no TaskBeavo fee, and it
 * counts once Stripe's webhook says so (`tip.status` SUCCEEDED, hard rule a).
 */

/** The tip chips of mockup 7d. None is preselected: tipping is the poster's own idea. */
export const TIP_CHOICES_CENTS = [200, 500, 1000] as const;
/** The smallest tip the server takes; the largest is the task price. */
export const MIN_TIP_CENTS = 100;

/** The chips that fit under the task price. */
export function tipChoices(priceCents: number): number[] {
  return TIP_CHOICES_CENTS.filter((cents) => cents <= priceCents);
}

/** Whether the server would take this tip: whole cents, €1 up to the task price. */
export function tipCheck(cents: number, priceCents: number): boolean {
  return Number.isInteger(cents) && cents >= MIN_TIP_CENTS && cents <= priceCents;
}

/** The confirmation's lines (mockup 7e): the task price, the tip and what goes to the helper. */
export function releaseSummary(priceCents: number, tipCents: number | null) {
  const tip = tipCents ?? 0;
  return { priceCents, tipCents: tip, totalCents: priceCents + tip };
}

// "20", "20.5", "20,50", with an optional € sign; no thousands separators.
const EUROS = /^(?:€\s*)?(\d+)(?:[.,](\d{1,2}))?$/;

/** Euros typed by the poster as whole cents, or null when it isn't an amount. */
export function parseEuroCents(text: string): number | null {
  const match = EUROS.exec(text.trim());
  if (!match) return null;
  const cents = (match[2] ?? '').padEnd(2, '0');
  return Number(match[1]) * 100 + Number(cents);
}

export type PartialReleaseCheck =
  { ok: true; releasedCents: number } | { ok: false; problem: 'format' | 'range' };

/** A lower amount is more than nothing and less than the price; the full price is a release. */
export function partialReleaseCheck(text: string, priceCents: number): PartialReleaseCheck {
  const cents = parseEuroCents(text);
  if (cents === null) return { ok: false, problem: 'format' };
  if (cents < 1 || cents >= priceCents) return { ok: false, problem: 'range' };
  return { ok: true, releasedCents: cents };
}

export type ReleaseBooking = Pick<
  ReleaseFieldsFragment,
  'canReleasePayment' | 'releaseProposal' | 'tip'
> & {
  status: BookingStatus;
  /** The task price: what the helper gets on a full release. */
  amountCents: number;
};

export type TipState =
  | { kind: 'none'; choices: number[] }
  /** Started, not confirmed by Stripe yet: pay it again with the same amount. */
  | { kind: 'pending'; amountCents: number }
  | { kind: 'paid'; amountCents: number }
  /** The booking moved on (closed); no tip any more. */
  | { kind: 'closed' };

/**
 * - `release`: Release payment, the tip chips and "Release a lower amount" (7d); `declined`
 *   when the helper turned down the last proposal.
 * - `proposalOpen`: the poster's proposal waits for the helper; withdraw it, or release in full.
 * - `partialSettled`: the helper accepted a lower amount; `done` once Stripe settled it.
 * - `released`: released in full (7f), with where the tip stands.
 * - `notRequested`: the helper hasn't requested payment yet.
 * - `answer`: the helper accepts or declines the poster's lower amount.
 * - `helperWaiting` / `helperReleased`: the helper's view of the request and the release.
 * - `unavailable`: anything else (disputed, refunded, a late arrival to answer first…).
 */
export type ReleaseView =
  | {
      kind: 'release';
      priceCents: number;
      declined: { releasedCents: number } | null;
      tipChoices: number[];
    }
  | {
      kind: 'proposalOpen';
      priceCents: number;
      releasedCents: number;
      refundCents: number | null;
      canRelease: boolean;
    }
  | { kind: 'partialSettled'; releasedCents: number; refundCents: number | null; done: boolean }
  | { kind: 'released'; priceCents: number; tip: TipState }
  | { kind: 'notRequested' }
  | { kind: 'answer'; priceCents: number; releasedCents: number }
  | { kind: 'helperWaiting'; priceCents: number }
  | { kind: 'helperReleased'; priceCents: number; tipCents: number | null }
  | { kind: 'unavailable' }
  | { kind: 'unknownViewer' };

const RELEASED: readonly BookingStatus[] = ['RELEASED', 'PAID_OUT', 'CLOSED'];
const TIPPABLE: readonly BookingStatus[] = ['RELEASED', 'PAID_OUT'];

export function releaseView(booking: ReleaseBooking, role: BookingRole): ReleaseView {
  if (role === 'unknown') return { kind: 'unknownViewer' };
  const { status, amountCents: priceCents, releaseProposal: proposal, tip } = booking;

  if (proposal?.status === 'ACCEPTED') {
    return {
      kind: 'partialSettled',
      releasedCents: proposal.releasedCents,
      refundCents: proposal.refundCents,
      done: status === 'PARTIALLY_SETTLED' || status === 'CLOSED',
    };
  }

  if (role === 'helper') {
    if (status === 'PAYMENT_REQUESTED' && proposal?.status === 'OPEN') {
      return { kind: 'answer', priceCents, releasedCents: proposal.releasedCents };
    }
    if (status === 'PAYMENT_REQUESTED') return { kind: 'helperWaiting', priceCents };
    if (RELEASED.includes(status)) {
      return {
        kind: 'helperReleased',
        priceCents,
        tipCents: tip?.status === 'SUCCEEDED' ? tip.amountCents : null,
      };
    }
    return { kind: 'unavailable' };
  }

  if (RELEASED.includes(status)) {
    return { kind: 'released', priceCents, tip: tipState(booking) };
  }
  if (status === 'FUNDS_HELD') return { kind: 'notRequested' };
  if (status !== 'PAYMENT_REQUESTED') return { kind: 'unavailable' };
  if (proposal?.status === 'OPEN') {
    return {
      kind: 'proposalOpen',
      priceCents,
      releasedCents: proposal.releasedCents,
      refundCents: proposal.refundCents,
      canRelease: booking.canReleasePayment,
    };
  }
  if (!booking.canReleasePayment) return { kind: 'unavailable' };
  return {
    kind: 'release',
    priceCents,
    declined: proposal?.status === 'DECLINED' ? { releasedCents: proposal.releasedCents } : null,
    tipChoices: tipChoices(priceCents),
  };
}

function tipState({ status, amountCents, tip }: ReleaseBooking): TipState {
  if (tip?.status === 'SUCCEEDED') return { kind: 'paid', amountCents: tip.amountCents };
  if (!TIPPABLE.includes(status)) return { kind: 'closed' };
  if (tip?.status === 'PENDING') return { kind: 'pending', amountCents: tip.amountCents };
  return { kind: 'none', choices: tipChoices(amountCents) };
}

import type { ArrivalFieldsFragment } from './generated/graphql';

/**
 * What the day-of-the-job screen shows (BEAV-124, mockup 18), from the booking as the server
 * has it. The `can*` flags are the server's (bookings/arrival.py); the screen only offers
 * what they allow, and the mutations check again. Every amount is the server's
 * (bookings/cancellation.py, hard rule g).
 *
 * - `helper`: the On my way / Running late / I've arrived taps (evidence only), "I can't
 *   come" (the helper cancellation, CancelAsHelper with CANT_COME), "Start now" before the
 *   booked start, and "I was there" within 24 h of the poster saying they didn't come.
 * - `poster`: the helper's taps, the answer to a late report (keep, or cancel for free),
 *   "Did your helper come?", the early start, and where a reported no-show stands.
 * - `unknownViewer`: the viewer's side isn't known; never show the other side's view.
 */
export type ArrivalView =
  | ({ kind: 'helper' } & HelperArrival)
  | ({ kind: 'poster' } & PosterArrival)
  | { kind: 'unknownViewer' };

export type ArrivalRole = 'poster' | 'helper' | 'unknown';

/** Where the helper is, from their own taps. */
export type ArrivalStatus = 'none' | 'onMyWay' | 'late' | 'arrived';

export interface NoShowState {
  /** When the helper's 24 hours to answer "I was there" end, an ISO date-time. */
  contestBy: string;
  contested: boolean;
  awaitingSupport: boolean;
  /** Uncontested, or support upheld it: the helper cancellation applies. */
  settled: boolean;
}

interface Shared {
  status: ArrivalStatus;
  /** The helper's latest estimate, an ISO date-time; null if none. */
  etaAt: string | null;
  earlyStartPending: boolean;
  earlyStartConfirmed: boolean;
  noShow: NoShowState | null;
  /** Support found the helper was there; the booking continues. */
  noShowRejected: boolean;
}

export interface HelperArrival extends Shared {
  canTap: boolean;
  /** On my way is a single tap; later changes go through Running late. */
  canMarkOnMyWay: boolean;
  canRequestEarlyStart: boolean;
  canContestNoShow: boolean;
}

export interface PosterArrival extends Shared {
  canAnswerLate: boolean;
  canConfirmCame: boolean;
  /** "No, they didn't come" with what the poster would get back, or null. */
  noShowOffer: { refundCents: number; creditCents: number } | null;
  canAnswerEarlyStart: boolean;
  /** Show `arrival.earlyStartBodyWithdrawal`: a registered business helper still inside
   * the 14-day withdrawal period, whose withdrawal right stays after an early start. */
  earlyStartWithdrawal: boolean;
}

/** The ETA choices in minutes the helper picks from (mockup 18a); the server allows 1-240. */
export const ETA_CHOICES: readonly number[] = [10, 20, 30, 45, 60];

type ArrivalBooking = Pick<ArrivalFieldsFragment, 'helperUserId' | 'arrival'>;

export function arrivalRole(booking: ArrivalBooking, viewerId: string | null): ArrivalRole {
  if (viewerId === null) return 'unknown';
  return booking.helperUserId === viewerId ? 'helper' : 'poster';
}

const iso = (value: unknown): string | null => (typeof value === 'string' ? value : null);

export function arrivalView(booking: ArrivalBooking, role: ArrivalRole): ArrivalView {
  if (role === 'unknown') return { kind: 'unknownViewer' };
  const a = booking.arrival;
  const contestBy = iso(a.noShowContestBy);
  const shared: Shared = {
    status: a.arrivedAt ? 'arrived' : a.lateReportedAt ? 'late' : a.onMyWayAt ? 'onMyWay' : 'none',
    etaAt: iso(a.etaAt),
    earlyStartPending: a.earlyStartPending,
    earlyStartConfirmed: a.earlyStartConfirmedAt != null,
    noShow:
      contestBy && !a.noShowRejected
        ? {
            contestBy,
            contested: a.noShowContestedAt != null,
            awaitingSupport: a.noShowAwaitingSupport,
            settled: a.noShowSettledAt != null,
          }
        : null,
    noShowRejected: a.noShowRejected,
  };
  if (role === 'helper') {
    return {
      kind: 'helper',
      ...shared,
      canTap: a.canTap,
      canMarkOnMyWay: a.canTap && !a.onMyWayAt && !a.lateReportedAt,
      canRequestEarlyStart: a.canRequestEarlyStart,
      canContestNoShow: a.canContestNoShow,
    };
  }
  return {
    kind: 'poster',
    ...shared,
    canAnswerLate: a.canAnswerLate,
    canConfirmCame: a.canConfirmCame,
    noShowOffer:
      a.canConfirmNoShow && a.noShowRefundCents != null
        ? { refundCents: a.noShowRefundCents, creditCents: a.noShowCreditCents ?? 0 }
        : null,
    canAnswerEarlyStart: a.canAnswerEarlyStart,
    earlyStartWithdrawal: a.earlyStartWithdrawal,
  };
}

/** Whole minutes from `now` until an ISO date-time, rounded up, never below 0. */
export function minutesUntil(at: string, now: Date = new Date()): number {
  const ms = new Date(at).getTime() - now.getTime();
  return Number.isFinite(ms) ? Math.max(0, Math.ceil(ms / 60_000)) : 0;
}

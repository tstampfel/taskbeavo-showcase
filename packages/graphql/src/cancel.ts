import type {
  CancellationCase,
  CancellationFieldsFragment,
  HelperCancelReason,
  NoFaultLabel,
  NoFaultReason,
  PosterCancelReason,
} from './generated/graphql';

/**
 * What the cancel screen shows (mockup 13), from the booking as the server has it. Every
 * amount is the server's (bookings/cancellation.py, hard rule g): the screen never works one
 * out. The screen is shared by the poster and the helper; `role` comes from the viewer.
 *
 * - `cancel` (poster): the money is held; show the reasons and the refund `preview`.
 *   `withdrawal` is set while the withdrawal button is offered too (a registered business
 *   helper, inside the 14 days), with when it ends. `noFault`: a no-fault reason may be cited.
 * - `helperCancel` (helper): the money is held; the poster gets `refundCents` back plus
 *   `posterCreditCents` from TaskBeavo, and the cancel counts against reliability.
 * - `noFaultPending`: a no-fault request waits for the other side (`canRespond` when that's
 *   the viewer) or for support (`awaitingSupport`); `respondBy` ends the 48 hours.
 * - `withdrawOnly`: the ordinary cancel is refused (`refusedWith`, e.g. WORK_STARTED) but the
 *   withdrawal button is still offered.
 * - `refused`: neither is possible now (`refusedWith`, e.g. after "Request payment").
 * - `done`: cancelled or withdrawn; `refunding` until Stripe confirms the refund,
 *   `awaitingSupport` while support works out the part already done. `canReopen`: the poster
 *   can re-open the task to the other offers (BEAV-123).
 * - `unavailable`: any other status (not paid yet, released, disputed…).
 * - `unknownViewer`: the viewer's side couldn't be worked out (the session didn't load);
 *   the screen shows an error and a retry, never the other side's view (R1-11).
 *
 * `creditBackCents`: the poster's TaskBeavo credit that comes back, all of it after any
 * cancellation or split (rule g); 0 for none, and always 0 for the helper.
 */
export type CancelView =
  | {
      kind: 'cancel';
      preview: CancelPreview;
      withdrawal: WithdrawalOffer | null;
      noFault: boolean;
    }
  | {
      kind: 'helperCancel';
      refundCents: number;
      posterCreditCents: number;
      noFault: boolean;
    }
  | ({ kind: 'noFaultPending' } & NoFaultPending)
  | { kind: 'withdrawOnly'; refusedWith: string; withdrawal: WithdrawalOffer }
  | { kind: 'refused'; refusedWith: string }
  | {
      kind: 'done';
      withdrawal: boolean;
      awaitingSupport: boolean;
      refunding: boolean;
      refundCents: number | null;
      helperCompensationCents: number | null;
      byHelper: boolean;
      posterCreditCents: number;
      creditBackCents: number;
      canReopen: boolean;
      reopened: boolean;
    }
  | { kind: 'unavailable' }
  | { kind: 'unknownViewer' };

export type CancelRole = 'poster' | 'helper' | 'unknown';

export interface CancelPreview {
  case: CancellationCase;
  chargeCents: number;
  refundCents: number;
  helperCompensationCents: number;
  keptFeeCents: number;
  creditBackCents: number;
  /** The full charge comes back. */
  full: boolean;
}

export interface WithdrawalOffer {
  /** When the 14-day withdrawal period ends, an ISO date-time; null if unknown. */
  endsAt: string | null;
}

export interface NoFaultPending {
  /** The specific reason, for the side that asked; null for the other side. */
  reason: NoFaultReason | null;
  /** What both sides are shown (GDPR Art. 9: hospitalisation and bereavement show as a
   * personal emergency). */
  label: NoFaultLabel;
  requestedByYou: boolean;
  canRespond: boolean;
  awaitingSupport: boolean;
  /** When the other side's 48 hours end, an ISO date-time. */
  respondBy: string;
}

/** The poster's ordinary reasons, in the mockup's order. Enum keys only: no free text. */
export const POSTER_CANCEL_REASONS: readonly PosterCancelReason[] = [
  'NO_LONGER_NEEDED',
  'DIFFERENT_DATE',
  'HELPER_ASKED',
  'OTHER',
];

/** The helper's reasons (mockup 13g). */
export const HELPER_CANCEL_REASONS: readonly HelperCancelReason[] = [
  'ILL',
  'DOUBLE_BOOKED',
  'OTHER',
];

/** The no-fault reasons either side may cite: a full refund once the other side agrees or
 * support upholds it (bookings/no_fault.py). */
export const NO_FAULT_REASONS: readonly NoFaultReason[] = [
  'HOSPITALISATION',
  'EMERGENCY',
  'BEREAVEMENT',
  'WEATHER',
  'MUTUAL_AGREEMENT',
];

type CancelBooking = Pick<
  CancellationFieldsFragment,
  | 'status'
  | 'helperUserId'
  | 'withdrawalAvailable'
  | 'withdrawalEndsAt'
  | 'cancelPreview'
  | 'cancellation'
  | 'helperCancelPreview'
  | 'noFaultRequest'
  | 'canRequestNoFault'
  | 'canReopenTask'
  | 'taskReopened'
>;

/** The viewer's side of the booking: the helper's user ID matches, or else the poster.
 * `unknown` until the viewer is known: never guess the poster (R1-11). */
export function cancelRole(
  booking: Pick<CancelBooking, 'helperUserId'>,
  viewerId: string | null,
): CancelRole {
  if (viewerId === null) return 'unknown';
  return booking.helperUserId === viewerId ? 'helper' : 'poster';
}

export function cancelView(booking: CancelBooking, role: CancelRole): CancelView {
  if (role === 'unknown') return { kind: 'unknownViewer' };
  const request = booking.noFaultRequest;
  if (request?.open) {
    return {
      kind: 'noFaultPending',
      reason: request.reason ?? null,
      label: request.label,
      requestedByYou: request.requestedByYou,
      canRespond: request.canRespond,
      awaitingSupport: request.awaitingSupport,
      respondBy: String(request.respondBy),
    };
  }
  const done = booking.cancellation;
  if (done) {
    return {
      kind: 'done',
      withdrawal: done.withdrawal,
      awaitingSupport: done.awaitingSupport,
      refunding: !done.confirmed,
      refundCents: done.refundCents,
      helperCompensationCents: done.helperCompensationCents,
      byHelper: done.requestedBy === 'HELPER',
      posterCreditCents: done.posterCreditCents,
      creditBackCents: done.creditBackCents ?? 0,
      canReopen: role === 'poster' && booking.canReopenTask,
      reopened: booking.taskReopened,
    };
  }
  return role === 'helper' ? helperView(booking) : posterView(booking);
}

function helperView(booking: CancelBooking): CancelView {
  const preview = booking.helperCancelPreview;
  if (!preview) return { kind: 'unavailable' };
  if (preview.refusedWith !== null) return { kind: 'refused', refusedWith: preview.refusedWith };
  return {
    kind: 'helperCancel',
    refundCents: preview.refundCents,
    posterCreditCents: preview.posterCreditCents,
    noFault: booking.canRequestNoFault,
  };
}

function posterView(booking: CancelBooking): CancelView {
  const withdrawal = booking.withdrawalAvailable
    ? { endsAt: typeof booking.withdrawalEndsAt === 'string' ? booking.withdrawalEndsAt : null }
    : null;
  const preview = booking.cancelPreview;
  if (preview === null) {
    return withdrawal
      ? { kind: 'withdrawOnly', refusedWith: 'WORK_STARTED', withdrawal }
      : { kind: 'unavailable' };
  }
  if (preview.refusedWith !== null || preview.case === null) {
    const refusedWith = preview.refusedWith ?? 'BOOKING_ACTION_NOT_ALLOWED';
    return withdrawal
      ? { kind: 'withdrawOnly', refusedWith, withdrawal }
      : { kind: 'refused', refusedWith };
  }
  return {
    kind: 'cancel',
    preview: {
      case: preview.case,
      chargeCents: preview.chargeCents,
      refundCents: preview.refundCents,
      helperCompensationCents: preview.helperCompensationCents,
      keptFeeCents: preview.keptFeeCents,
      creditBackCents: preview.creditBackCents,
      full: preview.refundCents === preview.chargeCents,
    },
    withdrawal,
    noFault: booking.canRequestNoFault,
  };
}

/** Support's decision on the latest no-fault request, once it is closed: both sides are
 * told whether the reason was accepted (user decision 2026-11-07). */
export function noFaultDecision(
  booking: Pick<CancelBooking, 'noFaultRequest'>,
): 'UPHELD' | 'REJECTED' | null {
  const request = booking.noFaultRequest;
  if (!request || request.open) return null;
  return request.decision ?? null;
}

/** Whole hours left until `respondBy`, rounded up, never below 0 ("47 h left"). */
export function hoursLeft(respondBy: string, now: Date = new Date()): number {
  const ms = new Date(respondBy).getTime() - now.getTime();
  return Number.isFinite(ms) ? Math.max(0, Math.ceil(ms / 3_600_000)) : 0;
}

/** A reason the screen offers: an ordinary one, or a no-fault one (its own request). */
export type PickedReason =
  | { kind: 'ordinary'; key: PosterCancelReason | HelperCancelReason }
  | { kind: 'noFault'; key: NoFaultReason };

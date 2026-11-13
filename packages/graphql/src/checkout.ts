import type { SubscribeToMoreFunction } from '@apollo/client';
import {
  BookingUpdatedDocument,
  type BookingFieldsFragment,
  type BookingQuery,
  type BookingQueryVariables,
  type CheckoutConsentInput,
  type CheckoutConsentVariant,
  type CheckoutPolicyQuery,
  type Locale,
  type SessionPlatform,
} from './generated/graphql';

/**
 * What a checkout screen shows (mockups 6a–6f), from the booking as the server has it. The
 * client never decides a payment is made: `confirming` waits for the webhook's FUNDS_HELD
 * (hard rule a), however sure Stripe's sheet was.
 *
 * - `pay`: open; show the summary and the payment sheet. `decline` is set after a declined
 *   card or a failed 3D Secure check, to show above it.
 * - `authenticating`: the bank asked to confirm in 3D Secure or the banking app (6e).
 * - `confirming`: Stripe took the payment; waiting for the webhook.
 * - `held`: the money is held (FUNDS_HELD or any later status, 6c).
 * - `expired`, `withdrawn`, `notStarted`: the checkout ended without a payment (6f).
 */
export type CheckoutView =
  | { kind: 'pay'; decline: string | null }
  | { kind: 'authenticating' }
  | { kind: 'confirming' }
  | { kind: 'held' }
  | { kind: 'expired' }
  | { kind: 'withdrawn' }
  | { kind: 'notStarted' };

type CheckoutBooking = Pick<
  BookingFieldsFragment,
  'status' | 'checkoutExpiresAt' | 'lastPaymentAttempt' | 'checkoutFailureReason'
>;

/**
 * What this client did with the payment. Attempts are ordered by ID, not by clock: a decline
 * counts against a submit only if it is a different attempt than the last one the screen had
 * seen when Stripe accepted the payment (review R1-2), so client and server clocks never
 * meet.
 */
export interface CheckoutProgress {
  /** Stripe accepted the payment (not declined); null until then. */
  submitted: Submitted | null;
  /** The sheet is open with a 3D Secure or banking-app challenge in front of the poster. */
  authenticating?: boolean;
}

export interface Submitted {
  /** `lastPaymentAttempt.id` as the screen had it at the submit; null for none. */
  lastAttemptId: string | null;
}

/** The submit marker for `booking` as the screen has it now. */
export function submittedAfter(booking: Pick<CheckoutBooking, 'lastPaymentAttempt'>): Submitted {
  return { lastAttemptId: booking.lastPaymentAttempt?.id ?? null };
}

/**
 * Stripe's `redirect_status` on the return from a bank's page: `submitted` when Stripe has
 * the payment (succeeded, or processing for slower methods), `failed` when it doesn't, null
 * when the page wasn't a return from Stripe.
 */
export function redirectOutcome(status: string | null): 'submitted' | 'failed' | null {
  if (status === 'succeeded' || status === 'processing') return 'submitted';
  if (status === null) return null;
  return 'failed';
}

const time = (value: unknown) => Date.parse(String(value));

export function checkoutView(
  booking: CheckoutBooking,
  progress: CheckoutProgress,
  now: Date,
): CheckoutView {
  if (booking.status === 'PAYMENT_FAILED') {
    if (booking.checkoutFailureReason === 'OFFER_WITHDRAWN') return { kind: 'withdrawn' };
    if (booking.checkoutFailureReason === 'PAYMENT_NOT_STARTED') return { kind: 'notStarted' };
    return { kind: 'expired' };
  }
  if (booking.status !== 'AWAITING_PAYMENT') return { kind: 'held' };
  const attempt = booking.lastPaymentAttempt;
  const { submitted } = progress;
  // A decline the webhook reported after the last submit sends the poster back to pay.
  const newerDecline =
    attempt?.kind === 'DECLINED' && (submitted === null || attempt.id !== submitted.lastAttemptId);
  if (submitted !== null && !newerDecline) return { kind: 'confirming' };
  if (booking.checkoutExpiresAt && time(booking.checkoutExpiresAt) <= now.getTime()) {
    // The expiry job hasn't ended it yet; it will, and the offer reopens.
    return { kind: 'expired' };
  }
  if (progress.authenticating) return { kind: 'authenticating' };
  return { kind: 'pay', decline: newerDecline ? declineKey(attempt.declineCode) : null };
}

/**
 * Stripe decline codes with their own message under `checkout.decline.*`; every other code
 * shows `checkout.decline.generic`. Codes that mean "don't tell the cardholder why" (lost or
 * stolen card, fraud) are deliberately generic, as Stripe advises.
 */
export const DECLINE_KEYS = [
  'insufficient_funds',
  'expired_card',
  'incorrect_cvc',
  'card_not_supported',
  'authentication_failed',
  'processing_error',
] as const;

export type DeclineKey = (typeof DECLINE_KEYS)[number] | 'generic';

const ALIASES: Readonly<Record<string, DeclineKey>> = {
  invalid_cvc: 'incorrect_cvc',
  payment_intent_authentication_failure: 'authentication_failed',
  authentication_required: 'authentication_failed',
  currency_not_supported: 'card_not_supported',
};

export function declineKey(code: string | null | undefined): DeclineKey {
  if (!code) return 'generic';
  if ((DECLINE_KEYS as readonly string[]).includes(code)) return code as DeclineKey;
  return ALIASES[code] ?? 'generic';
}

type Policy = NonNullable<CheckoutPolicyQuery['currentLegalDocument']>;

/** Both separate boxes; each starts unticked (hard rule g, legal-terms). */
export interface ConsentTicks {
  cancellationPolicy: boolean;
  earlyStart: boolean;
}

export const UNTICKED: ConsentTicks = { cancellationPolicy: false, earlyStart: false };

/**
 * Which of the policy's two tick-box pairs the offer's helper type shows (user decision
 * 2026-11-04): the server refuses the other one (CONSENT_WORDING_CHANGED).
 */
export function consentVariant(helperIsTrader: boolean): CheckoutConsentVariant {
  return helperIsTrader ? 'TRADER_HELPER' : 'PRIVATE_HELPER';
}

/** The lawyer's wording of the two boxes for this helper type; null when the policy has none. */
export function consentTexts(policy: Policy | null | undefined, helperIsTrader: boolean) {
  const consent = policy?.checkoutConsent;
  if (!consent) return null;
  return helperIsTrader ? consent.traderHelper : consent.privateHelper;
}

/** Pay is enabled only with both boxes ticked for a policy that carries their wording. */
export function canPay(policy: Policy | null | undefined, ticks: ConsentTicks): boolean {
  return Boolean(policy?.checkoutConsent) && ticks.cancellationPolicy && ticks.earlyStart;
}

/** acceptOffer's consent: the version, language and wording the screen showed. */
export function checkoutConsentInput(
  policy: Policy,
  ticks: ConsentTicks,
  platform: SessionPlatform,
  helperIsTrader: boolean,
): CheckoutConsentInput {
  return {
    variant: consentVariant(helperIsTrader),
    cancellationPolicyAccepted: ticks.cancellationPolicy,
    earlyStartRequested: ticks.earlyStart,
    cancellationVersion: policy.version,
    platform,
    language: policy.language as Locale,
  };
}

/** The GraphQL Locale enum value of an app language. */
export function graphqlLocale(locale: string): Locale {
  return locale.toUpperCase() as Locale;
}

/**
 * Keeps a Booking query live: every status change, and for the poster each card attempt,
 * arrives as the whole booking, which the normalised cache merges by ID. Missed events come
 * back with the refetch on reconnect and on return to the foreground.
 */
export function subscribeToBooking(
  subscribeToMore: SubscribeToMoreFunction<BookingQuery, BookingQueryVariables>,
  id: string,
): () => void {
  return subscribeToMore({ document: BookingUpdatedDocument, variables: { id } });
}

/** Whole seconds until the checkout ends, never negative; null when there is no end. */
export function secondsUntilExpiry(booking: CheckoutBooking, now: Date): number | null {
  if (!booking.checkoutExpiresAt) return null;
  return Math.max(0, Math.ceil((time(booking.checkoutExpiresAt) - now.getTime()) / 1000));
}

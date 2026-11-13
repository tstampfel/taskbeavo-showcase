import { describe, expect, it } from 'vitest';
import {
  UNTICKED,
  canPay,
  checkoutConsentInput,
  checkoutView,
  consentTexts,
  declineKey,
  redirectOutcome,
  secondsUntilExpiry,
  submittedAfter,
} from './checkout';

const now = new Date('2026-11-04T10:00:00Z');
const inTen = '2026-11-04T10:10:00Z';

function booking(extra: Record<string, unknown> = {}) {
  return {
    status: 'AWAITING_PAYMENT' as const,
    checkoutExpiresAt: inTen,
    lastPaymentAttempt: null,
    checkoutFailureReason: null,
    ...extra,
  } as Parameters<typeof checkoutView>[0];
}

const decline = (id: string, declineCode = 'insufficient_funds', at = '2026-11-04T09:58:00Z') => ({
  __typename: 'PaymentAttempt' as const,
  id,
  kind: 'DECLINED' as const,
  declineCode,
  at,
});

const policy = {
  __typename: 'LegalDocument' as const,
  id: 'doc-de',
  kind: 'CANCELLATION' as const,
  version: '2026-11-c',
  language: 'DE' as const,
  effectiveAt: '2026-11-01T00:00:00Z',
  text: 'Stornobedingungen',
  checkoutConsent: {
    __typename: 'CheckoutConsentTexts' as const,
    privateHelper: {
      __typename: 'CheckoutConsentText' as const,
      cancellationPolicy: 'Ich akzeptiere die Stornobedingungen (privat).',
      earlyStart: 'Ich verlange den Beginn vor Ablauf der Widerrufsfrist (privat).',
    },
    traderHelper: {
      __typename: 'CheckoutConsentText' as const,
      cancellationPolicy: 'Ich akzeptiere die Stornobedingungen (Gewerbe).',
      earlyStart: 'Ich verlange den Beginn vor Ablauf der Widerrufsfrist (Gewerbe).',
    },
  },
};

const notSubmitted = { submitted: null };

describe('checkoutView', () => {
  it('asks to pay while the checkout is open', () => {
    expect(checkoutView(booking(), notSubmitted, now)).toEqual({
      kind: 'pay',
      decline: null,
    });
  });

  it('keeps confirming after Stripe took the payment until the webhook says FUNDS_HELD', () => {
    const progress = { submitted: submittedAfter(booking()) };

    expect(checkoutView(booking(), progress, now)).toEqual({ kind: 'confirming' });
    expect(checkoutView(booking({ status: 'FUNDS_HELD' }), progress, now)).toEqual({
      kind: 'held',
    });
  });

  it('shows a decline and lets the poster try again', () => {
    const attempt = decline('41');

    expect(checkoutView(booking({ lastPaymentAttempt: attempt }), notSubmitted, now)).toEqual({
      kind: 'pay',
      decline: 'insufficient_funds',
    });
  });

  it('goes back to pay when a decline arrives after the submit', () => {
    const progress = { submitted: submittedAfter(booking({ lastPaymentAttempt: decline('41') })) };
    // Its server time is before the client's submit, as with a skewed clock: order counts.
    const attempt = decline('42', 'do_not_honor', '2026-11-04T09:00:00Z');

    expect(checkoutView(booking({ lastPaymentAttempt: attempt }), progress, now)).toEqual({
      kind: 'pay',
      decline: 'generic',
    });
  });

  it('ignores a decline from before the last submit', () => {
    const attempt = decline('41', 'insufficient_funds', '2026-11-04T11:00:00Z');
    const progress = { submitted: submittedAfter(booking({ lastPaymentAttempt: attempt })) };

    expect(checkoutView(booking({ lastPaymentAttempt: attempt }), progress, now)).toEqual({
      kind: 'confirming',
    });
  });

  it('shows the banking-app wait during 3D Secure', () => {
    expect(checkoutView(booking(), { ...notSubmitted, authenticating: true }, now)).toEqual({
      kind: 'authenticating',
    });
  });

  it('is expired once checkoutExpiresAt passed, before the job ends it', () => {
    const late = new Date('2026-11-04T10:10:00Z');

    expect(checkoutView(booking(), notSubmitted, late)).toEqual({ kind: 'expired' });
  });

  it.each([
    ['CHECKOUT_EXPIRED', 'expired'],
    ['OFFER_WITHDRAWN', 'withdrawn'],
    ['PAYMENT_NOT_STARTED', 'notStarted'],
  ])('a checkout that failed with %s shows %s', (reason, kind) => {
    const failed = booking({ status: 'PAYMENT_FAILED', checkoutFailureReason: reason });

    expect(checkoutView(failed, notSubmitted, now)).toEqual({ kind });
  });
});

describe('redirectOutcome', () => {
  it.each([
    ['succeeded', 'submitted'],
    ['processing', 'submitted'],
    ['failed', 'failed'],
    ['requires_payment_method', 'failed'],
    [null, null],
  ] as const)('redirect_status %s → %s', (status, outcome) => {
    expect(redirectOutcome(status)).toBe(outcome);
  });

  it('a failed redirect with its decline already stored stays on pay with the reason', () => {
    // The return from the bank isn't a submit, so the stored decline shows (review R1-2).
    expect(redirectOutcome('failed')).not.toBe('submitted');
    expect(
      checkoutView(
        booking({ lastPaymentAttempt: decline('7', 'authentication_required') }),
        notSubmitted,
        now,
      ),
    ).toEqual({ kind: 'pay', decline: 'authentication_failed' });
  });
});

describe('declineKey', () => {
  it.each([
    ['insufficient_funds', 'insufficient_funds'],
    ['invalid_cvc', 'incorrect_cvc'],
    ['payment_intent_authentication_failure', 'authentication_failed'],
    ['stolen_card', 'generic'],
    ['unknown', 'generic'],
    [null, 'generic'],
  ])('%s → %s', (code, key) => {
    expect(declineKey(code)).toBe(key);
  });
});

describe('consent', () => {
  it('both boxes start unticked and Pay needs both', () => {
    expect(UNTICKED).toEqual({ cancellationPolicy: false, earlyStart: false });
    expect(canPay(policy, UNTICKED)).toBe(false);
    expect(canPay(policy, { cancellationPolicy: true, earlyStart: false })).toBe(false);
    expect(canPay(policy, { cancellationPolicy: false, earlyStart: true })).toBe(false);
    expect(canPay(policy, { cancellationPolicy: true, earlyStart: true })).toBe(true);
  });

  it('no policy, or one without the lawyer’s tick-box wording, can’t be paid', () => {
    const ticked = { cancellationPolicy: true, earlyStart: true };

    expect(canPay(null, ticked)).toBe(false);
    expect(canPay({ ...policy, checkoutConsent: null }, ticked)).toBe(false);
  });

  it('words the boxes for the offer’s helper type', () => {
    expect(consentTexts(policy, false)?.earlyStart).toContain('(privat)');
    expect(consentTexts(policy, true)?.earlyStart).toContain('(Gewerbe)');
    expect(consentTexts({ ...policy, checkoutConsent: null }, true)).toBeNull();
  });

  it('sends the version, language and wording the screen showed', () => {
    const ticked = { cancellationPolicy: true, earlyStart: true };

    expect(checkoutConsentInput(policy, ticked, 'WEB', true).variant).toBe('TRADER_HELPER');
    expect(checkoutConsentInput(policy, ticked, 'IOS', false)).toEqual({
      variant: 'PRIVATE_HELPER',
      cancellationPolicyAccepted: true,
      earlyStartRequested: true,
      cancellationVersion: '2026-11-c',
      platform: 'IOS',
      language: 'DE',
    });
  });
});

describe('secondsUntilExpiry', () => {
  it('counts down and stops at zero', () => {
    expect(secondsUntilExpiry(booking(), now)).toBe(600);
    expect(secondsUntilExpiry(booking(), new Date('2026-11-04T11:00:00Z'))).toBe(0);
    expect(secondsUntilExpiry(booking({ checkoutExpiresAt: null }), now)).toBeNull();
  });
});

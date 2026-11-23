import { describe, expect, it } from 'vitest';
import { stripeReturn } from './stripe-return';

const base = 'https://taskbeavo.example/en/bookings/b-1/release';

describe('stripeReturn', () => {
  it('is null on a normal page load', () => {
    expect(stripeReturn(new URL(`${base}?tab=x`))).toBeNull();
  });

  it.each([
    ['succeeded', 'submitted'],
    ['processing', 'submitted'],
    ['failed', 'failed'],
    ['requires_payment_method', 'failed'],
  ] as const)('redirect_status %s → %s, without Stripe parameters', (status, outcome) => {
    const result = stripeReturn(
      new URL(
        `${base}?tab=x&payment_intent=pi_1&payment_intent_client_secret=pi_1_secret_2&redirect_status=${status}`,
      ),
    );
    expect(result?.outcome).toBe(outcome);
    expect(result?.cleanUrl.toString()).toBe(`${base}?tab=x`);
  });

  it('a return without redirect_status counts as not paid', () => {
    expect(stripeReturn(new URL(`${base}?payment_intent=pi_1`))?.outcome).toBe('failed');
  });
});

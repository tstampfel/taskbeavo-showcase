import { describe, expect, it, vi } from 'vitest';
import {
  confirmDemoPayment,
  demoPaymentsOn,
  demoStripeBase,
  intentIdOf,
  type DemoFetch,
} from './demo-payments';

const SECRET = 'pi_demo0123abcd_secret_xyz';

function answer(status: number, body: unknown) {
  return vi.fn<DemoFetch>(async () => ({ ok: status < 400, json: async () => body }));
}

describe('demo payments', () => {
  it('is on only with the flag', () => {
    expect(demoPaymentsOn('1')).toBe(true);
    expect(demoPaymentsOn('true')).toBe(true);
    expect(demoPaymentsOn(undefined)).toBe(false);
    expect(demoPaymentsOn('0')).toBe(false);
  });

  it('reads the PaymentIntent ID from the client secret', () => {
    expect(intentIdOf(SECRET)).toBe('pi_demo0123abcd');
    expect(intentIdOf('nonsense')).toBeNull();
  });

  it('puts the stand-in next to the GraphQL endpoint', () => {
    expect(demoStripeBase('http://localhost:8000/graphql')).toBe(
      'http://localhost:8000/demo-stripe',
    );
  });

  it('posts the client secret and the chosen card', async () => {
    const fetcher = answer(200, { status: 'succeeded' });

    await expect(
      confirmDemoPayment('http://api/demo-stripe', SECRET, 'pays', fetcher),
    ).resolves.toBe('paid');
    expect(fetcher).toHaveBeenCalledWith(
      'http://api/demo-stripe/v1/payment_intents/pi_demo0123abcd/confirm',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ client_secret: SECRET, card_number: '4242424242424242' }),
      }),
    );
  });

  it('reports a decline and a failure', async () => {
    await expect(
      confirmDemoPayment(
        'b',
        SECRET,
        'declines',
        answer(200, { status: 'requires_payment_method' }),
      ),
    ).resolves.toBe('declined');
    await expect(confirmDemoPayment('b', SECRET, 'pays', answer(404, {}))).resolves.toBe('failed');
    const broken = vi.fn<DemoFetch>(async () => {
      throw new Error('offline');
    });
    await expect(confirmDemoPayment('b', SECRET, 'pays', broken)).resolves.toBe('failed');
  });
});

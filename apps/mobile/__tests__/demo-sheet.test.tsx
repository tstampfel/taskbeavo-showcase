import { render, screen } from '@testing-library/react-native';
import i18n from 'i18next';
import { demoPaymentSheet, demoPaymentsEnabled } from '../src/payments/demo-sheet';
import '../src/i18n';

const mockLoads: string[] = [];
jest.mock('@stripe/stripe-react-native', () => {
  mockLoads.push('stripe');
  return {};
});
jest.mock('../src/screens/checkout-screen', () => {
  const { Text } = jest.requireActual('react-native');
  return { CheckoutScreen: () => <Text testID="checkout-screen">checkout</Text> };
});
jest.mock('expo-router', () => ({ useRouter: () => ({ replace: jest.fn() }) }));

const SECRET = 'pi_demo1_secret_x';

function answering(status: string) {
  return jest.fn(async () => ({ ok: true, json: async () => ({ status }) }));
}

describe('the demo payment sheet (BEAV-198)', () => {
  afterEach(() => {
    delete process.env.EXPO_PUBLIC_DEMO_PAYMENTS;
  });

  it('is on only with EXPO_PUBLIC_DEMO_PAYMENTS', () => {
    expect(demoPaymentsEnabled('1')).toBe(true);
    expect(demoPaymentsEnabled(undefined)).toBe(false);
  });

  it('confirms with the chosen demo card at the stand-in', async () => {
    const fetcher = answering('succeeded');
    globalThis.fetch = fetcher as unknown as typeof fetch;
    const sheet = demoPaymentSheet(i18n.t, async () => 'pays', 'http://api/demo-stripe');

    await expect(sheet.pay(SECRET, 'Confirm')).resolves.toBe('paid');
    expect(fetcher).toHaveBeenCalledWith(
      'http://api/demo-stripe/v1/payment_intents/pi_demo1/confirm',
      expect.objectContaining({ method: 'POST' }),
    );
  });

  it('is closed without a card, and failed on a decline', async () => {
    globalThis.fetch = answering('requires_payment_method') as unknown as typeof fetch;

    await expect(demoPaymentSheet(i18n.t, async () => null, 'b').pay(SECRET, 'x')).resolves.toBe(
      'closed',
    );
    await expect(
      demoPaymentSheet(i18n.t, async () => 'declines', 'b').pay(SECRET, 'x'),
    ).resolves.toBe('failed');
  });

  it('opens the checkout without loading the Stripe SDK', async () => {
    process.env.EXPO_PUBLIC_DEMO_PAYMENTS = '1';
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const { CheckoutRoute } = require('../src/payments/checkout-route');

    await render(<CheckoutRoute bookingId="booking-1" />);

    expect(await screen.findByTestId('checkout-screen')).toBeTruthy();
    expect(mockLoads).toEqual([]);
  });
});

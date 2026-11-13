import { paymentSheetParams } from '../src/payments/stripe-config';

describe('paymentSheetParams', () => {
  it('labels the sheet button with the card-payment wording, not Stripe’s “Pay €X”', () => {
    // The order button is the checkout's; the sheet only confirms the card (review R3-1).
    const params = paymentSheetParams('pi_123_secret_456', 'Potvrdite plaćanje karticom 33,60 €');

    expect(params.primaryButtonLabel).toBe('Potvrdite plaćanje karticom 33,60 €');
    expect(params.paymentIntentClientSecret).toBe('pi_123_secret_456');
    expect(params.allowsDelayedPaymentMethods).toBe(false);
  });

  it('keeps Stripe Link off, so no Stripe login stays on the phone', () => {
    expect(paymentSheetParams('pi_1_secret_2', 'Pay').link).toEqual({ display: 'never' });
  });
});

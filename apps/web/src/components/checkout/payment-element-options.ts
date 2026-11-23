import type { StripePaymentElementOptions } from '@stripe/stripe-js';

/**
 * Stripe Link stays off: it would store a Stripe login on the device, which isn't strictly
 * necessary for the payment (BEAV-190). advancedFraudSignals stays at Stripe's default (on),
 * which is fine because Stripe.js only loads on the payment pages (STRIPE_JS_MODULES in
 * eslint.config.js): checkout and the poster's tip.
 */
export const PAYMENT_ELEMENT_OPTIONS = {
  layout: 'tabs',
  wallets: { link: 'never' },
} as const satisfies StripePaymentElementOptions;

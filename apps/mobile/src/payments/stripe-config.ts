import type { PaymentSheet as StripeSheet } from '@stripe/stripe-react-native';
import { colors } from '@taskbeavo/ui-tokens';
import Constants from 'expo-constants';

/**
 * Stripe's native payment sheet (BEAV-111): card, Apple Pay and Google Pay, with 3D Secure
 * handled inside the sheet. The sheet only confirms the PaymentIntent the server created;
 * whether the money is held is the server's to say (FUNDS_HELD from the webhook, hard rule a).
 *
 * The publishable key is public and set per build (EXPO_PUBLIC_STRIPE_PUBLISHABLE_KEY, a test
 * key everywhere but production). Without one, checkout says payments aren't set up.
 *
 * This module never imports the Stripe SDK itself (types only): importing
 * `@stripe/stripe-react-native` starts the native SDK, which sends Stripe's fraud signals and
 * stores Stripe's `m` cookie. That may happen only on the checkout screens, so the SDK lives in
 * ./stripe.tsx, which checkout-route.tsx loads lazily (BEAV-190).
 */

/** Apple Pay merchant ID, registered with Apple and in the Stripe Dashboard (app.config.ts). */
export const APPLE_MERCHANT_ID = 'merchant.com.taskbeavo';
/** Where a bank's own app returns to after a redirect-based confirmation. */
export const STRIPE_RETURN_URL = 'taskbeavo://stripe-redirect';
export const MERCHANT_NAME = 'TaskBeavo';

export function stripePublishableKey(): string | null {
  const key = process.env.EXPO_PUBLIC_STRIPE_PUBLISHABLE_KEY;
  return key && /^pk_(test|live)_\w+$/.test(key) ? key : null;
}

const isProduction = () => Constants.expoConfig?.extra?.appEnv === 'production';

/** What the checkout screen needs from the sheet, so tests can stand in for it. */
export interface PaymentSheet {
  /**
   * Shows the sheet for the PaymentIntent. `paid`: Stripe accepted the payment (the server
   * still has to confirm it); `closed`: the poster closed the sheet; `failed`: the sheet
   * couldn't be shown or ended in an error (a decline is retried inside the sheet).
   * `buttonLabel` replaces Stripe's "Pay €X" on the sheet's button: the order button is the
   * checkout's, this one only confirms the card payment (checkout.cardPay, review R3-1).
   */
  pay(clientSecret: string, buttonLabel: string): Promise<'paid' | 'closed' | 'failed'>;
}

/** The sheet's setup: Apple Pay and Google Pay keep their own button wording. */
export function paymentSheetParams(
  clientSecret: string,
  buttonLabel: string,
): StripeSheet.SetupParams {
  return {
    paymentIntentClientSecret: clientSecret,
    merchantDisplayName: MERCHANT_NAME,
    returnURL: STRIPE_RETURN_URL,
    primaryButtonLabel: buttonLabel,
    applePay: { merchantCountryCode: 'HR' },
    googlePay: { merchantCountryCode: 'HR', currencyCode: 'EUR', testEnv: !isProduction() },
    // Only methods that confirm at once; a SEPA debit would hold nothing for days.
    allowsDelayedPaymentMethods: false,
    // Stripe Link stays off: it would keep a Stripe login on the phone (BEAV-190).
    link: { display: 'never' as StripeSheet.LinkDisplay },
    appearance: { colors: { primary: colors.green } },
  };
}

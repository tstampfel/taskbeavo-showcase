import {
  confirmDemoPayment,
  demoPaymentsOn,
  demoStripeBase,
  type DemoCard,
} from '@taskbeavo/graphql';
import type { TFunction } from 'i18next';
import { Alert, Platform } from 'react-native';
import { graphqlUrl } from '../apollo/endpoint';
import type { PaymentSheet } from './stripe-config';

/**
 * Demo payments (BEAV-198): THIS IS A DEMO STUB. With EXPO_PUBLIC_DEMO_PAYMENTS=1 the API runs
 * without Stripe, and this dialog stands in for Stripe's payment sheet. It says plainly that no
 * real money moves, lets the poster pick a card that pays or one that is declined, and confirms
 * the PaymentIntent at the API's demo stand-in. The booking is held only once the server reports
 * FUNDS_HELD from the webhook, as with Stripe (hard rule a). The Stripe SDK never loads.
 */
export function demoPaymentsEnabled(
  flag: string | undefined = process.env.EXPO_PUBLIC_DEMO_PAYMENTS,
) {
  return demoPaymentsOn(flag);
}

type Ask = (t: TFunction, buttonLabel: string) => Promise<DemoCard | null>;

/** The dialog: "Demo payment, no real money", then a card or cancel. */
export const askForDemoCard: Ask = (t, buttonLabel) =>
  new Promise((resolve) => {
    Alert.alert(
      t('checkout.demo.label'),
      `${t('checkout.demo.note')}\n\n${buttonLabel}`,
      [
        { text: t('checkout.demo.pays'), onPress: () => resolve('pays') },
        { text: t('checkout.demo.declines'), onPress: () => resolve('declines') },
        { text: t('checkout.policy.close'), style: 'cancel', onPress: () => resolve(null) },
      ],
      { cancelable: true, onDismiss: () => resolve(null) },
    );
  });

export function demoPaymentSheet(
  t: TFunction,
  ask: Ask = askForDemoCard,
  base: string = demoStripeBase(graphqlUrl(Platform.OS)),
): PaymentSheet {
  return {
    async pay(clientSecret, buttonLabel) {
      const card = await ask(t, buttonLabel);
      if (!card) return 'closed';
      const outcome = await confirmDemoPayment(base, clientSecret, card, fetch);
      if (outcome === 'paid') return 'paid';
      if (outcome === 'failed') Alert.alert(t('checkout.demo.label'), t('checkout.demo.failed'));
      // A decline comes back through the webhook's payment_intent.payment_failed, as with Stripe.
      return 'failed';
    },
  };
}

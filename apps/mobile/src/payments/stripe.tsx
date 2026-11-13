import { StripeProvider, useStripe } from '@stripe/stripe-react-native';
import * as Linking from 'expo-linking';
import { useCallback, useEffect, type ReactNode } from 'react';
import {
  APPLE_MERCHANT_ID,
  STRIPE_RETURN_URL,
  paymentSheetParams,
  type PaymentSheet,
} from './stripe-config';

/**
 * The Stripe SDK itself. Importing this module starts Stripe's native SDK (fraud signals and
 * Stripe's `m` cookie), so only checkout-route.tsx loads it, lazily, when a checkout screen
 * opens; nothing else may import it (BEAV-190, __tests__/stripe-lazy.test.tsx).
 */

export function usePaymentSheet(): PaymentSheet {
  const { initPaymentSheet, presentPaymentSheet } = useStripe();
  const pay = useCallback(
    async (clientSecret: string, buttonLabel: string) => {
      const init = await initPaymentSheet(paymentSheetParams(clientSecret, buttonLabel));
      if (init.error) return 'failed';
      const { error } = await presentPaymentSheet();
      if (!error) return 'paid';
      return error.code === 'Canceled' ? 'closed' : 'failed';
    },
    [initPaymentSheet, presentPaymentSheet],
  );
  return { pay };
}

/** Hands a bank app's return link (STRIPE_RETURN_URL) back to the sheet. */
function RedirectHandler() {
  const { handleURLCallback } = useStripe();
  useEffect(() => {
    const subscription = Linking.addEventListener('url', ({ url }) => {
      if (url.startsWith(STRIPE_RETURN_URL)) void handleURLCallback(url);
    });
    return () => subscription.remove();
  }, [handleURLCallback]);
  return null;
}

function WithSheet({ children }: { children: (sheet: PaymentSheet) => ReactNode }) {
  return <>{children(usePaymentSheet())}</>;
}

/** Stripe's provider with the payment sheet, for one checkout screen (loaded with React.lazy). */
export default function StripeCheckout({
  publishableKey,
  children,
}: {
  publishableKey: string;
  children: (sheet: PaymentSheet) => ReactNode;
}) {
  return (
    <StripeProvider
      publishableKey={publishableKey}
      merchantIdentifier={APPLE_MERCHANT_ID}
      urlScheme="taskbeavo"
    >
      <>
        <RedirectHandler />
        <WithSheet>{children}</WithSheet>
      </>
    </StripeProvider>
  );
}

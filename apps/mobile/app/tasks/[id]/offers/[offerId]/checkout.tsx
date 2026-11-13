import { useLocalSearchParams, useRouter } from 'expo-router';
import { CheckoutRoute } from '../../../../../src/payments/checkout-route';

/** Checkout for one offer (mockup 6a): consent, then Stripe's payment sheet. */
export default function OfferCheckoutRoute() {
  const router = useRouter();
  const { id, offerId } = useLocalSearchParams<{ id: string; offerId: string }>();
  return (
    <CheckoutRoute
      taskId={id}
      offerId={offerId}
      onPaymentStarted={(bookingId) =>
        router.replace({ pathname: '/bookings/[id]/checkout', params: { id: bookingId } })
      }
    />
  );
}

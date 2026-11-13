import { useLocalSearchParams } from 'expo-router';
import { CheckoutRoute } from '../../../src/payments/checkout-route';

/** An open checkout or its outcome (mockups 6b–6f), on return or from a push. */
export default function BookingCheckoutRoute() {
  const { id } = useLocalSearchParams<{ id: string }>();
  return <CheckoutRoute bookingId={id} />;
}

import { useLocalSearchParams, useRouter } from 'expo-router';
import { PaymentSheetScope } from '../../../src/payments/checkout-route';
import { ReleaseScreen } from '../../../src/screens/release-screen';

/**
 * Release payment (mockups 7d–7f), from the booking, the payment-requested push and links.
 * Opening it never releases anything: the poster confirms with a tap and the server checks
 * the booking. The helper answers a proposal to release less here too.
 */
export default function ReleasePaymentRoute() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  return (
    <ReleaseScreen
      bookingId={id}
      PaymentScope={PaymentSheetScope}
      onBack={() => router.replace({ pathname: '/bookings/[id]', params: { id } })}
    />
  );
}

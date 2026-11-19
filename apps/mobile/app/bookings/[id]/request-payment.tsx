import { useLocalSearchParams, useRouter } from 'expo-router';
import { RequestPaymentScreen } from '../../../src/screens/request-payment-screen';

/** The helper's Request payment (mockup 7a), from the booking detail screen. */
export default function RequestPaymentRoute() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  return (
    <RequestPaymentScreen
      bookingId={id}
      onBack={() => router.replace({ pathname: '/bookings/[id]', params: { id } })}
    />
  );
}

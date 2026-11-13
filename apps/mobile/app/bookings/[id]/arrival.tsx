import { useLocalSearchParams, useRouter } from 'expo-router';
import { ArrivalScreen } from '../../../src/screens/arrival-screen';

/** The day of the job for both sides (mockup 18); arrival pushes open it. */
export default function BookingArrivalRoute() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  return (
    <ArrivalScreen
      bookingId={id}
      onBack={() => router.replace({ pathname: '/bookings/[id]', params: { id } })}
    />
  );
}

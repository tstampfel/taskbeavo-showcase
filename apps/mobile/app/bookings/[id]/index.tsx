import { useLocalSearchParams, useRouter } from 'expo-router';
import { BookingDetailScreen } from '../../../src/screens/booking-detail-screen';

/** Booking detail for both sides (mockup 05); booking links and pushes open it. */
export default function BookingDetailRoute() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  return (
    <BookingDetailScreen
      bookingId={id}
      onOpen={(screen) => router.push({ pathname: `/bookings/[id]/${screen}`, params: { id } })}
    />
  );
}

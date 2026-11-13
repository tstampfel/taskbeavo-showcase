import { useLocalSearchParams, useRouter } from 'expo-router';
import { openLegal } from '../../../src/auth/app-auth';
import { CancelScreen } from '../../../src/screens/cancel-screen';

/** The poster's cancel and withdrawal button (mockup 13). */
export default function BookingCancelRoute() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  return (
    <CancelScreen
      bookingId={id}
      onOpenPolicy={(locale) => openLegal('cancellation', locale)}
      onBack={() => router.replace({ pathname: '/bookings/[id]', params: { id } })}
    />
  );
}

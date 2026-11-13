import { useRouter } from 'expo-router';
import { useCallback } from 'react';
import { BusinessDetailsScreen } from '../../src/screens/business-details-screen';

/** Settings → Business details: the obrt or company a poster books for (BEAV-199). */
export default function BusinessDetailsRoute() {
  const router = useRouter();
  const onSignedOut = useCallback(() => router.replace('/sign-in'), [router]);
  return <BusinessDetailsScreen onBack={() => router.back()} onSignedOut={onSignedOut} />;
}

import { useLocalSearchParams, useRouter } from 'expo-router';
import { useCallback } from 'react';
import { nextTarget } from '../src/links/next-target';
import { PhoneScreen } from '../src/screens/phone-screen';

/** Verify your phone (mockups 15f, 15g), right after sign-up or before posting. */
export default function VerifyPhoneRoute() {
  const router = useRouter();
  const { next } = useLocalSearchParams<{ next?: string }>();
  const target = nextTarget(next);
  const onSignedOut = useCallback(() => router.replace('/sign-in'), [router]);
  return <PhoneScreen onDone={() => router.dismissTo(target)} onSignedOut={onSignedOut} />;
}

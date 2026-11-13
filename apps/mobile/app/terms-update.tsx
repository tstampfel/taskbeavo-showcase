import { useLocalSearchParams, useRouter } from 'expo-router';
import { useCallback } from 'react';
import { openLegal } from '../src/auth/app-auth';
import { nextTarget } from '../src/links/next-target';
import { TermsUpdateScreen } from '../src/screens/terms-update-screen';

/** We've updated our terms (mockup 21b): the way out of limited mode after a terms change. */
export default function TermsUpdateRoute() {
  const router = useRouter();
  const { next } = useLocalSearchParams<{ next?: string }>();
  const target = nextTarget(next);
  const onDone = useCallback(() => router.dismissTo(target), [router, target]);
  const onSignedOut = useCallback(() => router.replace('/sign-in'), [router]);
  return <TermsUpdateScreen onDone={onDone} onSignedOut={onSignedOut} openLegal={openLegal} />;
}

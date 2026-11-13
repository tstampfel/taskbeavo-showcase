import { needsPhoneVerification } from '@taskbeavo/graphql';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useEffect, useState } from 'react';
import {
  availableProviders,
  deviceLanguageTags,
  deviceTimeZone,
  openLegal,
  signInApi,
} from '../src/auth/app-auth';
import { appLanguage } from '../src/i18n/language';
import { nextTarget } from '../src/links/next-target';
import { SignInScreen, type SignInScreenProps } from '../src/screens/sign-in-screen';

/** Sign-in and sign-up (mockups 15a to 15e2). `next` is where a link wanted to go. */
export default function SignInRoute() {
  const router = useRouter();
  const { next } = useLocalSearchParams<{ next?: string }>();
  const target = nextTarget(next);
  const [providers, setProviders] = useState<SignInScreenProps['providers']>({});

  useEffect(() => {
    let live = true;
    void availableProviders().then((found) => live && setProviders(found));
    return () => {
      live = false;
    };
  }, []);

  return (
    <SignInScreen
      api={signInApi}
      providers={providers}
      language={appLanguage}
      timeZone={deviceTimeZone()}
      deviceLanguageTags={deviceLanguageTags()}
      openLegal={openLegal}
      onSignedIn={(user) => {
        if (needsPhoneVerification(user)) {
          router.replace({ pathname: '/verify-phone', params: { next: String(target) } });
        } else {
          // Back to the screen under sign-in (home) rather than a second copy of it.
          router.dismissTo(target);
        }
      }}
    />
  );
}

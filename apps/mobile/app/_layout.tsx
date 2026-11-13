import { ApolloProvider } from '@apollo/client/react';
import { Manrope_400Regular, Manrope_700Bold, useFonts } from '@expo-google-fonts/manrope';
import { colors } from '@taskbeavo/ui-tokens';
import * as Sentry from '@sentry/react-native';
import { Stack, type ErrorBoundaryProps } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { StatusBar } from 'expo-status-bar';
import { useEffect, useState } from 'react';
import { I18nextProvider } from 'react-i18next';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { apolloClient, restoreAppCache } from '../src/apollo/client';
import { useRefetchOnForeground } from '../src/apollo/foreground';
import { ErrorScreen } from '../src/components/error-screen';
import { fonts } from '../src/fonts';
import { i18n } from '../src/i18n';
import { initSentry } from '../src/sentry';

initSentry();
void SplashScreen.preventAutoHideAsync();
// Started at import so the cache loads while fonts do; it never rejects.
const cacheRestored = restoreAppCache();

/** A render error anywhere in the app: reported, then a translated retry screen. */
export function ErrorBoundary({ error, retry }: ErrorBoundaryProps) {
  useEffect(() => {
    Sentry.captureException(error);
    void SplashScreen.hideAsync();
  }, [error]);
  return <ErrorScreen onRetry={() => void retry()} />;
}

export default function RootLayout() {
  const [fontsLoaded, fontError] = useFonts({
    [fonts.regular]: Manrope_400Regular,
    [fonts.bold]: Manrope_700Bold,
  });
  const [cacheReady, setCacheReady] = useState(false);
  useEffect(() => {
    void cacheRestored.then(() => setCacheReady(true));
  }, []);
  // Screens query only after the saved cache is in, so they don't fetch what it holds.
  const ready = (fontsLoaded || fontError !== null) && cacheReady;
  useRefetchOnForeground(apolloClient);

  useEffect(() => {
    if (ready) void SplashScreen.hideAsync();
  }, [ready]);

  if (!ready) return null;

  return (
    <ApolloProvider client={apolloClient}>
      <I18nextProvider i18n={i18n}>
        <SafeAreaProvider>
          <StatusBar style="dark" />
          <Stack
            screenOptions={{
              headerShown: false,
              contentStyle: { backgroundColor: colors.offWhite },
            }}
          />
        </SafeAreaProvider>
      </I18nextProvider>
    </ApolloProvider>
  );
}

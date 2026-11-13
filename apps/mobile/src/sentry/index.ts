import * as Sentry from '@sentry/react-native';
import Constants from 'expo-constants';
import { ANONYMOUS_USER, sentryOptions } from './options';

/** Starts Sentry once, before the first screen renders. The DSN is an EAS environment variable. */
export function initSentry(): void {
  Sentry.init(
    sentryOptions({
      dsn: process.env.EXPO_PUBLIC_SENTRY_DSN,
      environment: Constants.expoConfig?.extra?.appEnv as string | undefined,
    }),
  );
  // Reaches the native scope, so native crash reports don't take the installation ID as user.
  Sentry.setUser(ANONYMOUS_USER);
}

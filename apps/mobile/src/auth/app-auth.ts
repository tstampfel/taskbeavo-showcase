import { feesUrl, legalUrl, rankingUrl, type LegalPage } from '@taskbeavo/app-links';
import { SignOutEverywhereDocument, type DeviceInput } from '@taskbeavo/graphql';
import type { Locale } from '@taskbeavo/i18n';
import { getCalendars, getLocales } from 'expo-localization';
import * as WebBrowser from 'expo-web-browser';
import { Platform } from 'react-native';
import { apolloClient, resetAppClientState, sessionManager } from '../apollo/client';
import type { ProviderSignIn } from '../screens/sign-in-screen';
import { appleSignInAvailable, signInWithApple } from './providers/apple';
import { googleSignInAvailable, signInWithGoogle } from './providers/google';
import { createSignInApi } from './sign-in-api';
import { secureDeviceTokenStore } from './token-store';

/** The session list shows "iPhone" or "Android"; the phone's own name may hold the owner's. */
const device: DeviceInput =
  Platform.OS === 'ios'
    ? { platform: 'IOS', name: 'iPhone' }
    : { platform: 'ANDROID', name: 'Android' };

export const signInApi = createSignInApi(
  apolloClient,
  sessionManager,
  device,
  secureDeviceTokenStore,
);

export async function availableProviders(): Promise<{
  apple?: ProviderSignIn;
  google?: ProviderSignIn;
}> {
  return {
    ...((await appleSignInAvailable()) ? { apple: signInWithApple } : {}),
    ...(googleSignInAvailable() ? { google: signInWithGoogle } : {}),
  };
}

export function deviceTimeZone(): string {
  return getCalendars()[0]?.timeZone ?? 'Europe/Zagreb';
}

export function deviceLanguageTags(): string[] {
  return getLocales().map((locale) => locale.languageTag);
}

/** The legal pages are web pages (BEAV-81), shown in the in-app browser in `locale`. */
export function openLegal(page: LegalPage, locale: Locale): void {
  void WebBrowser.openBrowserAsync(legalUrl(page, locale));
}

/** The service fee price list (BEAV-191), a web page like the legal pages. */
export function openFees(locale: Locale): void {
  void WebBrowser.openBrowserAsync(feesUrl(locale));
}

/** How offers are ordered (BEAV-191, CRD art. 6a), a web page like the legal pages. */
export function openRanking(locale: Locale): void {
  void WebBrowser.openBrowserAsync(rankingUrl(locale));
}

export function signOutHere(): Promise<void> {
  return resetAppClientState('signOut');
}

/**
 * Log out of all devices: the API revokes every session of the account (this one too) and
 * closes their WebSockets, then this phone forgets the account as on sign-out. Throws when
 * the API couldn't do it, so the screen can say so; the phone stays signed in then.
 */
export async function signOutEverywhere(): Promise<void> {
  await apolloClient.mutate({ mutation: SignOutEverywhereDocument });
  await resetAppClientState('signOut');
}

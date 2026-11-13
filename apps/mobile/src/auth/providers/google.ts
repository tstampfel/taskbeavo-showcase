import { AuthRequest, ResponseType, exchangeCodeAsync } from 'expo-auth-session';
import { Platform } from 'react-native';
import { signUpWithGoogle } from 'react-native-credentials-manager';
import type { ProviderCredential } from '../sign-in-api';

/**
 * Google sign-in, one way per platform, both behind the same two functions and both with our
 * nonce in the ID token:
 *
 * - iOS: the system browser (expo-auth-session), the authorization-code flow with PKCE,
 *   exchanged for an ID token on the phone. Google's iOS clients redirect to the reversed
 *   client ID scheme and need no secret.
 * - Android: Credential Manager (react-native-credentials-manager), which asks Google Play
 *   services for an ID token directly. Its audience is the web client ID; Google matches the
 *   app to its Android OAuth client by package name and signing certificate. A browser flow
 *   can't work here: Android clients have no redirect URI, and a web client with an https App
 *   Link redirect needs a client secret for the code exchange, which an app can't keep.
 */
export const googleDiscovery = {
  authorizationEndpoint: 'https://accounts.google.com/o/oauth2/v2/auth',
  tokenEndpoint: 'https://oauth2.googleapis.com/token',
};

type GoogleEnv = Record<string, string | undefined>;

// Spelled out so Expo inlines them into the bundle.
const buildEnv: GoogleEnv = {
  EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID: process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID,
  EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID: process.env.EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID,
};

/** The client ID this platform asks Google with: the iOS client, or on Android the web one. */
export function googleClientId(
  os: string = Platform.OS,
  env: GoogleEnv = buildEnv,
): string | undefined {
  const id =
    os === 'ios' ? env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID : env.EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID;
  return id || undefined;
}

/** "123-abc.apps.googleusercontent.com" → "com.googleusercontent.apps.123-abc:/oauthredirect". */
export function googleRedirectUri(clientId: string): string {
  return `${clientId.split('.').reverse().join('.')}:/oauthredirect`;
}

export function googleSignInAvailable(): boolean {
  return googleClientId() !== undefined;
}

/** Null when the user closes the browser or the account sheet. */
export async function signInWithGoogle(nonce: string): Promise<ProviderCredential | null> {
  const clientId = googleClientId();
  if (!clientId) throw new Error('Google sign-in is not configured');
  return Platform.OS === 'android'
    ? signInWithCredentialManager(clientId, nonce)
    : signInWithBrowser(clientId, nonce);
}

async function signInWithCredentialManager(
  webClientId: string,
  nonce: string,
): Promise<ProviderCredential | null> {
  try {
    const credential = await signUpWithGoogle({
      serverClientId: webClientId,
      nonce,
      autoSelectEnabled: false,
      filterByAuthorizedAccounts: false,
    });
    if (!credential.idToken) throw new Error('Google returned no ID token');
    return {
      idToken: credential.idToken,
      firstName: credential.givenName ?? '',
      lastName: credential.familyName ?? '',
    };
  } catch (error) {
    if (isCancellation(error)) return null;
    throw error;
  }
}

/**
 * The messages a dismissed account sheet comes back with. react-native-credentials-manager
 * rejects every failure with the code "ERROR" and only the exception's message, so the
 * GetCredentialCancellationException class can't be seen from here; these are the messages
 * Play services and the Android framework give it. Anything else (a wrong SHA-1 or package,
 * no Google account) is a real failure and shows an error.
 */
const CANCELLATION_MESSAGES = [
  /^activity is cancell?ed by the user\.?$/i,
  /^\[?16\]?:? ?cancell?ed by (the )?user\.?$/i,
  /failure response from one tap: 16: cancell?ed by (the )?user/i,
];

export function isCancellation(error: unknown): boolean {
  const message = (error instanceof Error ? error.message : String(error)).trim();
  return CANCELLATION_MESSAGES.some((pattern) => pattern.test(message));
}

async function signInWithBrowser(
  clientId: string,
  nonce: string,
): Promise<ProviderCredential | null> {
  const redirectUri = googleRedirectUri(clientId);
  const request = new AuthRequest({
    clientId,
    redirectUri,
    scopes: ['openid', 'email', 'profile'],
    responseType: ResponseType.Code,
    usePKCE: true,
    extraParams: { nonce, prompt: 'select_account' },
  });
  const result = await request.promptAsync(googleDiscovery);
  if (result.type === 'cancel' || result.type === 'dismiss') return null;
  if (result.type !== 'success') throw new Error(`Google sign-in ended with ${result.type}`);
  const code = result.params.code;
  if (!code || !request.codeVerifier) throw new Error('Google returned no code');
  const tokens = await exchangeCodeAsync(
    { clientId, redirectUri, code, extraParams: { code_verifier: request.codeVerifier } },
    googleDiscovery,
  );
  if (!tokens.idToken) throw new Error('Google returned no ID token');
  return { idToken: tokens.idToken };
}

import * as AppleAuthentication from 'expo-apple-authentication';
import { Platform } from 'react-native';
import type { ProviderCredential } from '../sign-in-api';

/** Sign in with Apple is native on iOS only; on Android it isn't offered. */
export async function appleSignInAvailable(): Promise<boolean> {
  if (Platform.OS !== 'ios') return false;
  try {
    return await AppleAuthentication.isAvailableAsync();
  } catch {
    return false;
  }
}

/**
 * Apple's native sheet, with our nonce in the ID token. Apple sends the name only the first
 * time, so it goes to the API with the token. Null when the user cancels.
 */
export async function signInWithApple(nonce: string): Promise<ProviderCredential | null> {
  try {
    const credential = await AppleAuthentication.signInAsync({
      requestedScopes: [
        AppleAuthentication.AppleAuthenticationScope.FULL_NAME,
        AppleAuthentication.AppleAuthenticationScope.EMAIL,
      ],
      nonce,
    });
    if (!credential.identityToken) throw new Error('Apple returned no identity token');
    return {
      idToken: credential.identityToken,
      firstName: credential.fullName?.givenName ?? '',
      lastName: credential.fullName?.familyName ?? '',
    };
  } catch (error) {
    if ((error as { code?: unknown } | null)?.code === 'ERR_REQUEST_CANCELED') return null;
    throw error;
  }
}

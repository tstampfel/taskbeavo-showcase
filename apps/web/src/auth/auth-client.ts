import {
  SignInError,
  type AccountDetailsInput,
  type SignInAnswer,
  type SignInProvider,
  type ViewerFieldsFragment,
} from '@taskbeavo/graphql';
import { SAME_ORIGIN_HEADERS } from './csrf';

/**
 * The sign-in screens' calls to the /api/auth route handlers. Those keep the session tokens
 * in httpOnly cookies and answer without them, so nothing here, or anywhere in page code,
 * ever holds a token. API errors throw a SignInError with the code; a network failure throws
 * one without a code.
 */

type Fetch = typeof fetch;

interface Body<TData> {
  data?: TData | null;
  errors?: { message?: string; extensions?: { code?: unknown; retryAfterSeconds?: unknown } }[];
}

/** "Firefox on Mac": what the API shows in the session list and the new-device email. */
export function browserDeviceName(userAgent: string): string {
  const browser = /Edg\//.test(userAgent)
    ? 'Edge'
    : /Firefox\//.test(userAgent)
      ? 'Firefox'
      : /Chrome\//.test(userAgent)
        ? 'Chrome'
        : /Safari\//.test(userAgent)
          ? 'Safari'
          : 'Browser';
  const system = /iPhone|iPad/.test(userAgent)
    ? 'iOS'
    : /Android/.test(userAgent)
      ? 'Android'
      : /Mac OS X/.test(userAgent)
        ? 'Mac'
        : /Windows/.test(userAgent)
          ? 'Windows'
          : /Linux/.test(userAgent)
            ? 'Linux'
            : '';
  return system ? `${browser} on ${system}` : browser;
}

async function post<TData>(
  path: string,
  body: Record<string, unknown>,
  fetchImpl: Fetch,
): Promise<TData> {
  let parsed: Body<TData>;
  try {
    const response = await fetchImpl(path, {
      method: 'POST',
      headers: SAME_ORIGIN_HEADERS,
      body: JSON.stringify(body),
      credentials: 'same-origin',
    });
    parsed = (await response.json()) as Body<TData>;
  } catch {
    throw new SignInError(undefined);
  }
  if (parsed.data) return parsed.data;
  throw SignInError.fromErrors(parsed.errors);
}

export interface SignedIn {
  __typename: 'AuthSession';
  id: string;
  user: ViewerFieldsFragment;
}

export interface ProviderSignIn {
  provider: SignInProvider;
  idToken: string;
  nonce: string;
  /** Apple shares the name only on the first sign-in. */
  firstName?: string;
  lastName?: string;
}

export function createAuthClient(
  fetchImpl: Fetch = (...args) => fetch(...args),
  deviceName = typeof navigator === 'undefined' ? '' : browserDeviceName(navigator.userAgent),
) {
  return {
    async verifyEmailCode(email: string, code: string): Promise<SignInAnswer> {
      const data = await post<{ verifyEmailCode: SignInAnswer }>(
        '/api/auth/verify-email-code',
        { email, code, deviceName },
        fetchImpl,
      );
      return data.verifyEmailCode;
    },
    async signInWithProvider(input: ProviderSignIn): Promise<SignInAnswer> {
      const data = await post<{ signInWithProvider: SignInAnswer }>(
        '/api/auth/sign-in-with-provider',
        { ...input, deviceName },
        fetchImpl,
      );
      return data.signInWithProvider;
    },
    async linkProvider(ticket: string, code: string): Promise<SignedIn> {
      const data = await post<{ linkProviderWithEmailCode: SignedIn }>(
        '/api/auth/link-provider',
        { ticket, code, deviceName },
        fetchImpl,
      );
      return data.linkProviderWithEmailCode;
    },
    async completeAccountDetails(details: AccountDetailsInput): Promise<SignedIn> {
      const data = await post<{ completeAccountDetails: SignedIn }>(
        '/api/auth/complete-account-details',
        { details, deviceName },
        fetchImpl,
      );
      return data.completeAccountDetails;
    },
    /** Always ends the browser session: the route handler clears the cookies regardless. */
    async signOut(): Promise<void> {
      try {
        await post('/api/auth/sign-out', {}, fetchImpl);
      } catch {
        // Offline: the cookies stay until the next sign-out or their expiry.
      }
    },
    /** Log out of all devices. Throws a SignInError when it didn't happen. */
    async signOutEverywhere(): Promise<void> {
      await post('/api/auth/sign-out-everywhere', {}, fetchImpl);
    },
  };
}

export type AuthClient = ReturnType<typeof createAuthClient>;

/**
 * Where to go after signing in: `next` when it is a path on this site (so a crafted link
 * can't send the user elsewhere), else the home page. The path has no locale prefix.
 */
export function safeNextPath(next: string | null | undefined): string {
  if (!next) return '/';
  // A locale prefix is dropped first (the router adds the account's), so the checks below
  // see the path that is actually used: "/de//evil.example" must not become "//evil.example".
  const path = next.replace(/^\/(hr|en|de|fr)(?=\/|$)/, '') || '/';
  if (!path.startsWith('/') || path.startsWith('//') || path.includes('\\')) return '/';
  return path;
}

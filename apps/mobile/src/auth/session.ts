import type { AuthSessionFieldsFragment } from '@taskbeavo/graphql';

/** What the app keeps of a session; the user comes from the `viewer` query. */
export interface StoredSession {
  accessToken: string;
  /** ISO timestamp. */
  accessTokenExpiresAt: string;
  refreshToken: string;
}

/** Persists the session. In the app this is SecureStore (token-store.ts). */
export interface SessionStore {
  load(): Promise<StoredSession | null>;
  save(session: StoredSession): Promise<void>;
  clear(): Promise<void>;
}

/**
 * Trades a refresh token for a new session. Returns null when the API refused it
 * (SESSION_EXPIRED: sign in again); throws when the API couldn't be reached.
 */
export type Refresh = (refreshToken: string) => Promise<StoredSession | null>;

/** Refresh this long before the access token expires, so it doesn't die in flight. */
const REFRESH_MARGIN_MS = 30_000;

export function storedSession(
  session: Pick<AuthSessionFieldsFragment, 'accessToken' | 'accessTokenExpiresAt' | 'refreshToken'>,
): StoredSession {
  return {
    accessToken: session.accessToken,
    accessTokenExpiresAt: String(session.accessTokenExpiresAt),
    refreshToken: session.refreshToken,
  };
}

export interface SessionManager {
  /** A live access token, refreshed when needed, or null when signed out. */
  accessToken(): Promise<string | null>;
  /** Store the session a sign-in mutation returned. */
  signedIn(session: StoredSession): Promise<void>;
  /** Forget the session; returns the refresh token so the caller can call `signOut`. */
  signedOut(): Promise<string | null>;
}

/**
 * Hands out access tokens for the Apollo links. Concurrent callers share one refresh,
 * since a refresh token works once: a second refresh with the same token would count as
 * reuse and, after the API's grace period, revoke the session.
 */
export function createSessionManager(
  store: SessionStore,
  refresh: Refresh,
  now: () => number = Date.now,
  /**
   * The API ended the session (SESSION_EXPIRED): called once, after the tokens are deleted,
   * so the app clears what the account left on the phone. Not awaited.
   */
  onExpired: () => void = () => undefined,
): SessionManager {
  let pending: Promise<string | null> | null = null;

  async function refreshed(session: StoredSession): Promise<string | null> {
    let next: StoredSession | null;
    try {
      next = await refresh(session.refreshToken);
    } catch {
      // Offline: keep the session and go on signed out until the next try.
      return null;
    }
    if (next === null) {
      await store.clear();
      onExpired();
      return null;
    }
    await store.save(next);
    return next.accessToken;
  }

  return {
    accessToken() {
      // Callers arriving while a lookup is in flight share it, so the check and the
      // refresh happen once.
      pending ??= (async () => {
        const session = await store.load();
        if (session === null) return null;
        const expires = new Date(session.accessTokenExpiresAt).getTime();
        if (expires - REFRESH_MARGIN_MS > now()) return session.accessToken;
        return refreshed(session);
      })().finally(() => {
        pending = null;
      });
      return pending;
    },
    async signedIn(session) {
      await store.save(session);
    },
    async signedOut() {
      const session = await store.load();
      await store.clear();
      return session?.refreshToken ?? null;
    },
  };
}

/** `Authorization: Bearer <access token>` on every HTTP operation while signed in. */
export function authHeaders(
  headers: Record<string, string> | undefined,
  accessToken: string | null,
): Record<string, string> {
  return accessToken ? { ...headers, authorization: `Bearer ${accessToken}` } : { ...headers };
}

import { SignOutDocument, UnregisterPushTokenDocument } from '@taskbeavo/graphql';
import type { CachePersistor } from 'apollo3-cache-persist';
import type { PushTokenStore } from '../push/token-store';
import type { MutationClient } from './sign-in-api';
import type { SessionManager } from './session';

export interface ResetClientStateDeps {
  client: MutationClient & { clearStore(): Promise<unknown> };
  sessions: Pick<SessionManager, 'signedOut'>;
  pushTokens: PushTokenStore;
  /** The persisted Apollo cache (apollo3-cache-persist). */
  persistor: Pick<CachePersistor<unknown>, 'pause' | 'purge' | 'resume'>;
  /** Forgets the language chosen on this phone; only account deletion does. */
  forgetLanguage: () => Promise<void>;
}

export type ResetReason = 'signOut' | 'accountDeleted';

/**
 * Leaves nothing of the account on this phone. Sign-out and account deletion both call it.
 * First the push token is unregistered while the session can still do it (the API refuses a
 * token still live for another account), then the session is revoked and its tokens deleted
 * from SecureStore, and finally the in-memory and persisted Apollo caches are emptied. Each
 * step runs even when an earlier one failed offline, so the phone is signed out regardless;
 * a persisted cache that survives is purged at the next launch, since no session is left.
 */
export async function resetClientState(
  { client, sessions, pushTokens, persistor, forgetLanguage }: ResetClientStateDeps,
  reason: ResetReason = 'signOut',
): Promise<void> {
  const pushToken = await pushTokens.load().catch(() => null);
  if (pushToken) {
    try {
      await client.mutate({
        mutation: UnregisterPushTokenDocument,
        variables: { token: pushToken },
      });
    } catch {
      // Offline: the API drops the token when Expo reports it dead, or at the next sign-out.
    }
  }
  const refreshToken = await sessions.signedOut();
  if (refreshToken) {
    try {
      await client.mutate({ mutation: SignOutDocument, variables: { refreshToken } });
    } catch {
      // Signed out on the phone; the session expires on its own.
    }
  }
  await pushTokens.clear().catch(() => undefined);
  // No save runs between the two clears.
  persistor.pause();
  try {
    await client.clearStore();
    await persistor.purge().catch(() => undefined);
  } finally {
    persistor.resume();
  }
  if (reason === 'accountDeleted') await forgetLanguage().catch(() => undefined);
}

/**
 * `resetClientState` with its dependencies bound, run once at a time: a call while a reset
 * for the same reason runs joins it (concurrent SESSION_EXPIRED answers clear once); a
 * different reason runs after it, so account deletion still forgets the language.
 */
export function createClientStateReset(
  deps: () => ResetClientStateDeps,
): (reason?: ResetReason) => Promise<void> {
  let running: { reason: ResetReason; done: Promise<void> } | null = null;
  return (reason = 'signOut') => {
    if (running?.reason === reason) return running.done;
    const done = (running?.done ?? Promise.resolve())
      .then(() => resetClientState(deps(), reason))
      .finally(() => {
        if (running?.done === done) running = null;
      });
    running = { reason, done };
    return done;
  };
}

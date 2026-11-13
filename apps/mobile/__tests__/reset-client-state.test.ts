import { gql, InMemoryCache } from '@apollo/client';
import { SignOutDocument, UnregisterPushTokenDocument, typePolicies } from '@taskbeavo/graphql';
import { CachePersistor } from 'apollo3-cache-persist';
import { restorePersistedCache } from '../src/apollo/cache-persist';
import {
  createClientStateReset,
  resetClientState,
  type ResetClientStateDeps,
} from '../src/auth/reset-client-state';
import { createSessionManager, type StoredSession } from '../src/auth/session';

function deps(pushToken: string | null, refreshToken: string | null = 'refresh-1') {
  const calls: string[] = [];
  const d = {
    client: {
      mutate: jest.fn(async ({ mutation }: { mutation: unknown }) => {
        calls.push(mutation === UnregisterPushTokenDocument ? 'unregisterPush' : 'signOut');
        return { data: {} };
      }),
      clearStore: jest.fn(async () => void calls.push('clearStore')),
    },
    sessions: { signedOut: jest.fn(async () => (calls.push('deleteTokens'), refreshToken)) },
    pushTokens: {
      load: jest.fn(async () => pushToken),
      save: jest.fn(async () => undefined),
      clear: jest.fn(async () => void calls.push('forgetPushToken')),
    },
    persistor: {
      pause: jest.fn(() => void calls.push('pausePersist')),
      purge: jest.fn(async () => void calls.push('purgeCache')),
      resume: jest.fn(() => void calls.push('resumePersist')),
    },
    forgetLanguage: jest.fn(async () => void calls.push('forgetLanguage')),
  };
  return { d: d as unknown as ResetClientStateDeps & typeof d, calls };
}

describe('resetClientState', () => {
  it('unregisters the push token while still signed in, then deletes tokens and both caches', async () => {
    const { d, calls } = deps('ExponentPushToken[abc]');

    await resetClientState(d);

    expect(calls).toEqual([
      'unregisterPush',
      'deleteTokens',
      'signOut',
      'forgetPushToken',
      'pausePersist',
      'clearStore',
      'purgeCache',
      'resumePersist',
    ]);
    expect(d.client.mutate).toHaveBeenCalledWith({
      mutation: UnregisterPushTokenDocument,
      variables: { token: 'ExponentPushToken[abc]' },
    });
    expect(d.client.mutate).toHaveBeenCalledWith({
      mutation: SignOutDocument,
      variables: { refreshToken: 'refresh-1' },
    });
  });

  it('keeps the language choice on sign-out but forgets it after account deletion', async () => {
    const signOut = deps(null);
    await resetClientState(signOut.d, 'signOut');
    expect(signOut.d.forgetLanguage).not.toHaveBeenCalled();

    const deletion = deps(null);
    await resetClientState(deletion.d, 'accountDeleted');
    expect(deletion.calls.at(-1)).toBe('forgetLanguage');
  });

  it('skips the push call without a registered token', async () => {
    const { d, calls } = deps(null);
    await resetClientState(d);
    expect(calls).not.toContain('unregisterPush');
    expect(calls).toContain('clearStore');
  });

  it('still signs the phone out and clears both caches when offline', async () => {
    const { d, calls } = deps('ExponentPushToken[abc]');
    d.client.mutate.mockRejectedValue(new TypeError('Network request failed'));

    await expect(resetClientState(d)).resolves.toBeUndefined();

    expect(d.sessions.signedOut).toHaveBeenCalled();
    expect(calls).toEqual(
      expect.arrayContaining(['deleteTokens', 'forgetPushToken', 'clearStore', 'purgeCache']),
    );
  });

  it('clears the in-memory cache even when the persisted purge fails', async () => {
    // The next launch purges it anyway, since no session is left (restorePersistedCache).
    const { d, calls } = deps(null);
    d.persistor.purge.mockRejectedValue(new Error('disk full'));

    await expect(resetClientState(d)).resolves.toBeUndefined();

    expect(calls).toEqual(expect.arrayContaining(['clearStore', 'resumePersist']));
  });
});

describe('createClientStateReset', () => {
  it('joins a reset already running for the same reason', async () => {
    const { d, calls } = deps(null);
    const reset = createClientStateReset(() => d);

    await Promise.all([reset('signOut'), reset('signOut')]);

    expect(calls.filter((call) => call === 'clearStore')).toHaveLength(1);
  });

  it('runs a different reason after the one running, so deletion still forgets the language', async () => {
    const { d, calls } = deps(null);
    const reset = createClientStateReset(() => d);

    await Promise.all([reset('signOut'), reset('accountDeleted')]);

    expect(calls.filter((call) => call === 'clearStore')).toHaveLength(2);
    expect(calls.at(-1)).toBe('forgetLanguage');
  });

  it('runs again once the previous reset finished', async () => {
    const { d, calls } = deps(null);
    const reset = createClientStateReset(() => d);
    await reset();
    await reset();
    expect(calls.filter((call) => call === 'clearStore')).toHaveLength(2);
  });
});

describe('a session the API ended (SESSION_EXPIRED)', () => {
  it('clears the stored push token and both caches, as a sign-out does', async () => {
    const expired: StoredSession = {
      accessToken: 'access-1',
      accessTokenExpiresAt: '2000-01-01T00:00:00Z',
      refreshToken: 'refresh-1',
    };
    let stored: StoredSession | null = expired;
    const store = {
      load: async () => stored,
      save: async (session: StoredSession) => void (stored = session),
      clear: async () => void (stored = null),
    };
    const { d, calls } = deps('ExponentPushToken[abc]');
    const reset = createClientStateReset(() => ({ ...d, sessions }));
    let resetDone: Promise<void> = Promise.resolve();
    const sessions = createSessionManager(
      store,
      async () => null,
      Date.now,
      () => {
        resetDone = reset('signOut');
      },
    );

    expect(await sessions.accessToken()).toBeNull();
    await resetDone;

    expect(stored).toBeNull();
    expect(calls).toEqual(expect.arrayContaining(['forgetPushToken', 'clearStore', 'purgeCache']));
    // Tokens were already gone, so there was no refresh token to revoke.
    expect(d.client.mutate).not.toHaveBeenCalledWith(
      expect.objectContaining({ mutation: SignOutDocument }),
    );
  });
});

/**
 * The acceptance check, with the real apollo3-cache-persist on a real Apollo 4 cache (the
 * library declares Apollo 3 only): after logout, a fresh start shows no previous user's data.
 */
describe('a fresh start after logout', () => {
  const VIEWER = gql`
    query Viewer {
      viewer {
        id
        firstName
      }
    }
  `;

  function memoryStorage() {
    const items = new Map<string, string>();
    return {
      items,
      getItem: async (key: string) => items.get(key) ?? null,
      setItem: async (key: string, value: string) => void items.set(key, value),
      removeItem: async (key: string) => void items.delete(key),
    };
  }

  function launch(storage: ReturnType<typeof memoryStorage>) {
    const cache = new InMemoryCache({ typePolicies });
    const persistor = new CachePersistor({ cache, storage, trigger: false, key: 'cache' });
    return { cache, persistor };
  }

  const viewer = { viewer: { __typename: 'User', id: 'u1', firstName: 'Ana' } };

  it('shows the signed-in user after a restart, and nothing after logout', async () => {
    const storage = memoryStorage();
    const versions = memoryStorage();

    // First run: signed in, the viewer query lands in the cache and is persisted.
    const first = launch(storage);
    await restorePersistedCache({ ...first, versions, hasSession: async () => true });
    first.cache.writeQuery({ query: VIEWER, data: viewer });
    await first.persistor.persist();

    // Restarting while still signed in brings the data back.
    const second = launch(storage);
    await restorePersistedCache({ ...second, versions, hasSession: async () => true });
    expect(second.cache.readQuery({ query: VIEWER })).toEqual(viewer);

    // Logout.
    const client = {
      mutate: jest.fn(async () => ({})),
      clearStore: async () => void second.cache.reset(),
    };
    await resetClientState({
      client,
      sessions: { signedOut: async () => null },
      pushTokens: {
        load: async () => null,
        save: async () => undefined,
        clear: async () => undefined,
      },
      persistor: second.persistor,
      forgetLanguage: async () => undefined,
    });
    expect(second.cache.readQuery({ query: VIEWER })).toBeNull();
    expect(storage.items.has('cache')).toBe(false);

    // Fresh start: nothing of the previous user.
    const third = launch(storage);
    await restorePersistedCache({ ...third, versions, hasSession: async () => false });
    expect(third.cache.readQuery({ query: VIEWER })).toBeNull();
    expect(third.cache.extract()).toEqual({});
  });

  it('does not restore a persisted cache left behind by an interrupted logout', async () => {
    const storage = memoryStorage();
    const versions = memoryStorage();
    const first = launch(storage);
    await restorePersistedCache({ ...first, versions, hasSession: async () => true });
    first.cache.writeQuery({ query: VIEWER, data: viewer });
    await first.persistor.persist();

    // Tokens deleted, then the app was killed before the purge.
    const next = launch(storage);
    await restorePersistedCache({ ...next, versions, hasSession: async () => false });
    expect(next.cache.readQuery({ query: VIEWER })).toBeNull();
    expect(storage.items.has('cache')).toBe(false);
  });
});

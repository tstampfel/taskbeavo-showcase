import { apiRefresh } from '../src/auth/refresh';
import {
  authHeaders,
  createSessionManager,
  type SessionStore,
  type StoredSession,
} from '../src/auth/session';

const NOW = Date.parse('2026-10-29T12:00:00Z');

function memoryStore(initial: StoredSession | null) {
  let value = initial;
  const store: SessionStore & { value: () => StoredSession | null } = {
    load: async () => value,
    save: async (session) => {
      value = session;
    },
    clear: async () => {
      value = null;
    },
    value: () => value,
  };
  return store;
}

const live: StoredSession = {
  accessToken: 'access-1',
  accessTokenExpiresAt: '2026-10-29T12:10:00Z',
  refreshToken: 'refresh-1',
};
const expiring: StoredSession = { ...live, accessTokenExpiresAt: '2026-10-29T12:00:20Z' };
const next: StoredSession = {
  accessToken: 'access-2',
  accessTokenExpiresAt: '2026-10-29T12:15:00Z',
  refreshToken: 'refresh-2',
};

describe('session manager', () => {
  it('is signed out with nothing stored', async () => {
    const manager = createSessionManager(memoryStore(null), jest.fn(), () => NOW);
    expect(await manager.accessToken()).toBeNull();
  });

  it('hands out a live access token without refreshing', async () => {
    const refresh = jest.fn();
    const manager = createSessionManager(memoryStore(live), refresh, () => NOW);
    expect(await manager.accessToken()).toBe('access-1');
    expect(refresh).not.toHaveBeenCalled();
  });

  it('refreshes a token about to expire and stores the rotated session', async () => {
    const store = memoryStore(expiring);
    const refresh = jest.fn(async () => next);
    const manager = createSessionManager(store, refresh, () => NOW);

    expect(await manager.accessToken()).toBe('access-2');
    expect(refresh).toHaveBeenCalledWith('refresh-1');
    expect(store.value()).toEqual(next);
  });

  it('shares one refresh between concurrent callers, since a refresh token works once', async () => {
    const refresh = jest.fn(async () => next);
    const manager = createSessionManager(memoryStore(expiring), refresh, () => NOW);

    const tokens = await Promise.all([manager.accessToken(), manager.accessToken()]);

    expect(tokens).toEqual(['access-2', 'access-2']);
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it('forgets the session when the API ended it, and reports it once', async () => {
    const store = memoryStore(expiring);
    const onExpired = jest.fn(() => expect(store.value()).toBeNull());
    const manager = createSessionManager(
      store,
      async () => null,
      () => NOW,
      onExpired,
    );

    const tokens = await Promise.all([manager.accessToken(), manager.accessToken()]);
    expect(await manager.accessToken()).toBeNull();

    expect(tokens).toEqual([null, null]);
    expect(store.value()).toBeNull();
    expect(onExpired).toHaveBeenCalledTimes(1);
  });

  it('keeps the session when offline', async () => {
    const store = memoryStore(expiring);
    const onExpired = jest.fn();
    const manager = createSessionManager(
      store,
      async () => Promise.reject(new TypeError('Network request failed')),
      () => NOW,
      onExpired,
    );

    expect(await manager.accessToken()).toBeNull();
    expect(store.value()).toEqual(expiring);
    expect(onExpired).not.toHaveBeenCalled();
  });

  it('stores a sign-in and forgets it on sign-out, returning the refresh token', async () => {
    const store = memoryStore(null);
    const manager = createSessionManager(store, jest.fn(), () => NOW);

    await manager.signedIn(live);
    expect(await manager.accessToken()).toBe('access-1');
    expect(await manager.signedOut()).toBe('refresh-1');
    expect(store.value()).toBeNull();
  });
});

describe('authHeaders', () => {
  it('adds the bearer token while signed in', () => {
    expect(authHeaders({ traceparent: 't' }, 'jwt')).toEqual({
      traceparent: 't',
      authorization: 'Bearer jwt',
    });
  });

  it('adds nothing when signed out', () => {
    expect(authHeaders(undefined, null)).toEqual({});
  });
});

describe('apiRefresh', () => {
  const respond = (body: unknown) => jest.fn(async () => ({ status: 200, json: async () => body }));

  it('returns the rotated session', async () => {
    const fetchMock = respond({
      data: { refreshSession: { ...next, id: 's', refreshTokenExpiresAt: 'x', user: {} } },
    });
    const refresh = apiRefresh('http://api/graphql', fetchMock as unknown as typeof fetch);

    expect(await refresh('refresh-1')).toEqual(next);
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(String(init.body)).variables).toEqual({ refreshToken: 'refresh-1' });
  });

  it('returns null on SESSION_EXPIRED', async () => {
    const fetchMock = respond({
      data: null,
      errors: [{ extensions: { code: 'SESSION_EXPIRED' } }],
    });
    const refresh = apiRefresh('http://api/graphql', fetchMock as unknown as typeof fetch);
    expect(await refresh('reused')).toBeNull();
  });

  it('throws on other errors so the session is kept', async () => {
    const fetchMock = respond({ data: null, errors: [{ extensions: { code: 'RATE_LIMITED' } }] });
    const refresh = apiRefresh('http://api/graphql', fetchMock as unknown as typeof fetch);
    await expect(refresh('refresh-1')).rejects.toThrow('RATE_LIMITED');
  });
});

import { restorePersistedCache, CACHE_SCHEMA_VERSION } from '../src/apollo/cache-persist';

function setup({
  storedVersion = CACHE_SCHEMA_VERSION as string | null,
  hasSession = (async () => true) as () => Promise<boolean>,
} = {}) {
  const calls: string[] = [];
  const versions = new Map<string, string>();
  if (storedVersion !== null) versions.set('taskbeavo.apolloCacheVersion', storedVersion);
  const deps = {
    persistor: {
      restore: jest.fn(async () => void calls.push('restore')),
      purge: jest.fn(async () => void calls.push('purge')),
    },
    versions: {
      getItem: jest.fn(async (key: string) => versions.get(key) ?? null),
      setItem: jest.fn(async (key: string, value: string) => void versions.set(key, value)),
    },
    hasSession,
  };
  return { deps, calls, versions };
}

describe('restorePersistedCache', () => {
  it('restores the saved cache when signed in and written by this schema version', async () => {
    const { deps, calls } = setup();
    await restorePersistedCache(deps);
    expect(calls).toEqual(['restore']);
  });

  it('purges a cache written under another schema version and records the new one', async () => {
    const { deps, calls, versions } = setup({ storedVersion: '0' });
    await restorePersistedCache(deps);
    expect(calls).toEqual(['purge']);
    expect(versions.get('taskbeavo.apolloCacheVersion')).toBe(CACHE_SCHEMA_VERSION);
  });

  it('purges a cache from before versioning existed', async () => {
    const { deps, calls } = setup({ storedVersion: null });
    await restorePersistedCache(deps);
    expect(calls).toEqual(['purge']);
  });

  it('purges instead of restoring when no one is signed in', async () => {
    // Covers a sign-out cut short (app killed, purge failed) before the cache was emptied.
    const { deps, calls } = setup({ hasSession: async () => false });
    await restorePersistedCache(deps);
    expect(calls).toEqual(['purge']);
  });

  it('starts empty without purging when the session cannot be read (phone locked)', async () => {
    const { deps, calls } = setup({
      hasSession: async () => {
        throw new Error('User interaction is not allowed');
      },
    });
    await restorePersistedCache(deps);
    expect(calls).toEqual([]);
  });

  it('purges a cache it cannot read, and still resolves', async () => {
    const { deps, calls } = setup();
    deps.persistor.restore.mockRejectedValueOnce(new SyntaxError('Unexpected end of JSON'));
    await expect(restorePersistedCache(deps)).resolves.toBeUndefined();
    expect(calls).toEqual(['purge']);
  });

  it('still resolves when storage fails entirely', async () => {
    const { deps } = setup({ storedVersion: '0' });
    deps.persistor.purge.mockRejectedValue(new Error('disk full'));
    await expect(restorePersistedCache(deps)).resolves.toBeUndefined();
  });
});

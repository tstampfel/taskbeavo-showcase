import type { InMemoryCache } from '@apollo/client';
import { withoutSensitiveData } from '@taskbeavo/graphql';
import { CachePersistor, type PersistentStorage } from 'apollo3-cache-persist';

/**
 * Bump when the type policies in `@taskbeavo/graphql` change how objects are identified or
 * merged, or NOT_PERSISTED gains a field: a cache written under another version is purged at
 * launch instead of misread or kept with data that must no longer be on disk.
 * 2: sensitive fields left out (NOT_PERSISTED).
 * 3: Offer.message left out too.
 * 4: OnSiteContact name, area and phone left out too.
 */
export const CACHE_SCHEMA_VERSION = '4';

/**
 * The largest cache saved, in characters. A bigger one is purged and nothing more is saved
 * until the app restarts; the app works from the network meanwhile. 1 MB stays well inside
 * Android AsyncStorage's per-entry and total limits.
 */
export const CACHE_MAX_SIZE = 1024 * 1024;

export const CACHE_KEY = 'taskbeavo.apolloCache';
const VERSION_KEY = 'taskbeavo.apolloCacheVersion';

/**
 * Saves `cache` to `storage` after each write, without NOT_PERSISTED (exact addresses, access
 * details, phone numbers, chat text, credentials): those stay in memory and are refetched.
 */
export function createCachePersistor(
  cache: InMemoryCache,
  storage: PersistentStorage<string>,
  options: { trigger?: false } = {},
): CachePersistor<unknown> {
  return new CachePersistor({
    cache,
    storage,
    key: CACHE_KEY,
    maxSize: CACHE_MAX_SIZE,
    persistenceMapper: async (serialized: string) => withoutSensitiveData(serialized),
    ...options,
  });
}

export interface CacheVersionStore {
  getItem(key: string): Promise<string | null>;
  setItem(key: string, value: string): Promise<void>;
}

export interface RestoreDeps {
  persistor: Pick<CachePersistor<unknown>, 'restore' | 'purge'>;
  versions: CacheVersionStore;
  /** Whether a session is stored; throws when it can't be read (iOS, phone locked). */
  hasSession: () => Promise<boolean>;
}

/**
 * Loads the saved cache at launch, before any screen queries it. Only a signed-in phone
 * restores: with no session the saved cache is purged, so a fresh start after logout never
 * shows the previous user's data, even when the logout was cut short before its own purge.
 * A cache from another CACHE_SCHEMA_VERSION, or one that can't be read, is purged too.
 * Never rejects: the app can always start from an empty cache.
 */
export async function restorePersistedCache({
  persistor,
  versions,
  hasSession,
}: RestoreDeps): Promise<void> {
  let signedIn: boolean;
  try {
    signedIn = await hasSession();
  } catch {
    // The session may still be there; start empty and leave the saved cache alone.
    return;
  }
  try {
    const version = await versions.getItem(VERSION_KEY);
    if (!signedIn || version !== CACHE_SCHEMA_VERSION) {
      await persistor.purge();
      await versions.setItem(VERSION_KEY, CACHE_SCHEMA_VERSION);
      return;
    }
    await persistor.restore();
  } catch {
    await persistor.purge().catch(() => undefined);
  }
}

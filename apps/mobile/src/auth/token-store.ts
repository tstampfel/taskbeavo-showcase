import * as SecureStore from 'expo-secure-store';
import type { SessionStore, StoredSession } from './session';
import type { DeviceTokenStore } from './sign-in-api';

const KEY = 'taskbeavo.session';

/**
 * The session in the Keychain (iOS) or Keystore-encrypted storage (Android), readable
 * only while the device is unlocked and never synced or restored to another device.
 */
export const secureSessionStore: SessionStore = {
  async load() {
    const raw = await SecureStore.getItemAsync(KEY);
    if (raw === null) return null;
    try {
      return JSON.parse(raw) as StoredSession;
    } catch {
      return null;
    }
  },
  async save(session) {
    await SecureStore.setItemAsync(KEY, JSON.stringify(session), {
      keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
    });
  },
  async clear() {
    await SecureStore.deleteItemAsync(KEY);
  },
};

const DEVICE_KEY = 'taskbeavo.device';

/**
 * The API's device token for this phone. Kept through sign-out (it is no credential: it only
 * tells the API this phone signed in before); never synced or restored to another device.
 */
export const secureDeviceTokenStore: DeviceTokenStore = {
  async load() {
    return SecureStore.getItemAsync(DEVICE_KEY);
  },
  async save(token) {
    await SecureStore.setItemAsync(DEVICE_KEY, token, {
      keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
    });
  },
};

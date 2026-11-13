import * as SecureStore from 'expo-secure-store';

const KEY = 'taskbeavo.pushToken';

/**
 * This phone's Expo push token, as last registered with `registerPushToken`. Whatever
 * registers the token saves it here, so sign-out can unregister it: the API refuses a token
 * that is still live for another account, which would leave the next account on this phone
 * without pushes.
 */
export interface PushTokenStore {
  load(): Promise<string | null>;
  save(token: string): Promise<void>;
  clear(): Promise<void>;
}

export const securePushTokenStore: PushTokenStore = {
  load: () => SecureStore.getItemAsync(KEY),
  save: (token) => SecureStore.setItemAsync(KEY, token),
  clear: () => SecureStore.deleteItemAsync(KEY),
};

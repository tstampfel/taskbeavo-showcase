import AsyncStorage from '@react-native-async-storage/async-storage';
import * as SecureStore from 'expo-secure-store';
import { createTaskDraftStore, type KeyValueStore } from './task-draft-store';

// Readable only while the phone is unlocked: nothing needs the draft in the background.
const OPTIONS: SecureStore.SecureStoreOptions = {
  keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
};

const secureStorage: KeyValueStore = {
  getItem: (key) => SecureStore.getItemAsync(key, OPTIONS),
  setItem: (key, value) => SecureStore.setItemAsync(key, value, OPTIONS),
  removeItem: (key) => SecureStore.deleteItemAsync(key, OPTIONS),
};

/** The post-a-task draft on this phone; cleared on logout and account deletion. */
export const deviceTaskDraftStore = createTaskDraftStore(AsyncStorage, secureStorage);

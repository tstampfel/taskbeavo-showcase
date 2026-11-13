import * as SecureStore from 'expo-secure-store';
import type { LocaleStore, PickedLocaleStore } from './preference';

const KEY = 'taskbeavo.locale';

// Not a secret: readable after the first unlock, so a launch while the phone is locked (push,
// background task) still opens in the chosen language.
const OPTIONS: SecureStore.SecureStoreOptions = {
  keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK,
};

/** The chosen language, read synchronously at launch so the first screen is already in it. */
export const secureLocaleStore: LocaleStore = {
  load: () => SecureStore.getItem(KEY, OPTIONS),
  save: (locale) => SecureStore.setItem(KEY, locale, OPTIONS),
};

const PICKED_KEY = 'taskbeavo.localePicked';

/** Whether that language was picked with the switcher (see `PickedLocaleStore`). */
export const securePickedLocaleStore: PickedLocaleStore = {
  load: () => SecureStore.getItem(PICKED_KEY, OPTIONS),
  save: (locale) => {
    if (locale === null) void SecureStore.deleteItemAsync(PICKED_KEY, OPTIONS);
    else SecureStore.setItem(PICKED_KEY, locale, OPTIONS);
  },
};

/** Forgets the chosen language, after account deletion; the phone's language applies again. */
export async function forgetLocaleChoice(): Promise<void> {
  await SecureStore.deleteItemAsync(KEY, OPTIONS);
  await SecureStore.deleteItemAsync(PICKED_KEY, OPTIONS);
}

import { isLocale, type Locale } from '@taskbeavo/i18n';

/**
 * The language picked explicitly with the language switcher in this browser. The URL and the
 * locale cookie can't tell a pick from a language followed from browser settings or a shared
 * link, so sign-in reads this to decide whether to ask before changing the account's
 * language (`signInLocaleDecision`). Only a language code is stored, never anything personal.
 */
const KEY = 'taskbeavo.pickedLocale';

type Storage = Pick<globalThis.Storage, 'getItem' | 'setItem' | 'removeItem'>;

function storage(): Storage | undefined {
  try {
    return typeof window === 'undefined' ? undefined : window.localStorage;
  } catch {
    // Storage blocked (private mode, cookies off): the account's language wins.
    return undefined;
  }
}

export function loadPickedLocale(store: Storage | undefined = storage()): Locale | null {
  try {
    const value = store?.getItem(KEY) ?? null;
    return value !== null && isLocale(value) ? value : null;
  } catch {
    return null;
  }
}

export function savePickedLocale(
  locale: Locale | null,
  store: Storage | undefined = storage(),
): void {
  try {
    if (locale === null) store?.removeItem(KEY);
    else store?.setItem(KEY, locale);
  } catch {
    // Not remembered; the account's language wins at the next sign-in.
  }
}

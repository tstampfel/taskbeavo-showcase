import {
  createAccountLocaleSaver,
  fromApiLocale,
  isLocale,
  signInLocaleDecision,
  toApiLocale,
  type ApiLocale,
  type Locale,
  type SignInLocaleDecision,
} from '@taskbeavo/i18n';
import { resolveLocale } from './locale';

/** Where the app remembers the language the user picked. */
export interface LocaleStore {
  load(): string | null;
  save(locale: Locale): void;
}

/**
 * The language picked explicitly with the switcher, as opposed to one followed from the phone
 * or adopted from the account. Sign-in reads it to decide whether to ask before changing the
 * account's language.
 */
export interface PickedLocaleStore {
  load(): string | null;
  save(locale: Locale | null): void;
}

export interface LanguageDeps {
  i18n: { changeLanguage(locale: Locale): Promise<unknown> };
  store: LocaleStore;
  picked: PickedLocaleStore;
  /** Saves the language on the signed-in account (`setLocale`); does nothing signed out. */
  saveToAccount(locale: ApiLocale): Promise<void>;
}

/** The language the user chose before on this phone, else the phone's language. */
export function initialLocale(store: LocaleStore, deviceLanguageTags: readonly string[]): Locale {
  let saved: string | null = null;
  try {
    saved = store.load();
  } catch {
    // An unreadable store is no reason not to start; the phone language will do.
  }
  return typeof saved === 'string' && isLocale(saved) ? saved : resolveLocale(deviceLanguageTags);
}

/** One latest-wins account saver per wiring, so quick successive picks can't race. */
const accountSavers = new WeakMap<LanguageDeps, ReturnType<typeof createAccountLocaleSaver>>();

/**
 * The user picked a language: switch the app, remember it on the phone and save it on the
 * account, so emails, push and SMS follow. Account saves are latest-wins: tapping DE then FR
 * leaves the account on FR. Offline, the account keeps its old language until the user picks
 * again; the app still switches.
 */
export async function chooseLocale(locale: Locale, deps: LanguageDeps): Promise<void> {
  await adopt(locale, deps);
  remember(deps.picked, locale);
  let saveLatest = accountSavers.get(deps);
  if (saveLatest === undefined) {
    saveLatest = createAccountLocaleSaver();
    accountSavers.set(deps, saveLatest);
  }
  await saveLatest(toApiLocale(locale), deps.saveToAccount);
}

/**
 * The account's saved language (`User.locale`) becomes the app's, and the phone no longer
 * counts it as an explicit pick.
 */
export async function adoptAccountLocale(apiLocale: string, deps: LanguageDeps): Promise<void> {
  await adopt(fromApiLocale(apiLocale), deps);
  remember(deps.picked, null);
}

/**
 * Right after sign-in. Without an explicit pick on this phone (or with the same one), the
 * account's language becomes the app's. A different pick stays on screen and the account is
 * left alone: the caller asks "Also use … for emails and notifications?" and passes the
 * answer to `answerLocalePrompt`.
 */
export async function localeAfterSignIn(
  apiLocale: string,
  deps: LanguageDeps,
): Promise<SignInLocaleDecision> {
  let picked: string | null = null;
  try {
    picked = deps.picked.load();
  } catch {
    // Unreadable: treat it as no pick, so the account's language wins.
  }
  const decision = signInLocaleDecision(apiLocale, picked);
  if (decision.kind === 'useAccount') await adoptAccountLocale(apiLocale, deps);
  return decision;
}

/** Only a yes saves the picked language on the account. Offline, the account keeps its own. */
export async function answerLocalePrompt(
  yes: boolean,
  picked: Locale,
  deps: LanguageDeps,
): Promise<void> {
  if (!yes) return;
  try {
    await deps.saveToAccount(toApiLocale(picked));
  } catch {
    // The account keeps its language; the app still shows the pick.
  }
}

function remember(store: PickedLocaleStore, locale: Locale | null): void {
  try {
    store.save(locale);
  } catch {
    // Not remembered: the account's language wins at the next sign-in.
  }
}

async function adopt(locale: Locale, { i18n, store }: LanguageDeps): Promise<void> {
  await i18n.changeLanguage(locale);
  try {
    store.save(locale);
  } catch {
    // The app has switched; only the next launch may open in the old language.
  }
}

import { fromApiLocale, isLocale, type ApiLocale, type Locale } from './index';

/** Saves a language on the account (`setLocale`). */
export type SaveAccountLocale = (locale: ApiLocale) => Promise<unknown>;

/**
 * Latest-wins saving of the account language. Quick successive picks (DE, then FR) must not
 * race: if DE's request landed last, emails, push and SMS would go out in German while the
 * app shows French. Saves run one at a time, and a pick superseded by a newer one before its
 * turn is skipped. Every pick that is still the newest is sent, even if the same language was
 * sent before: the earlier save may have been signed out or for another account. Failures are
 * swallowed (the next pick retries); the returned promise never rejects.
 */
export function createAccountLocaleSaver() {
  let latestPick = 0;
  let queue: Promise<void> = Promise.resolve();

  return function saveLatest(locale: ApiLocale, save: SaveAccountLocale): Promise<void> {
    const pick = ++latestPick;
    queue = queue.then(async () => {
      if (pick !== latestPick) return;
      try {
        await save(locale);
      } catch {
        // Offline or signed out: the account keeps its language until the next pick.
      }
    });
    return queue;
  };
}

export type SignInLocaleDecision =
  /** No language was picked on this device, or the same one: the account's language wins. */
  | { kind: 'useAccount'; locale: Locale }
  /**
   * The user picked another language on this device while signed out: keep showing it, leave
   * the account alone, and ask once whether emails and notifications should follow it too.
   * Only a yes calls `setLocale`.
   */
  | { kind: 'askToSave'; picked: Locale; account: Locale };

/**
 * Which language to use right after sign-in. `picked` is the language chosen explicitly with
 * the language switcher on this device, if any; one only followed from the browser or phone
 * settings doesn't count.
 *
 * The question comes once per sign-in, not once ever: after "Not now" the device keeps its
 * pick and the account its language, so the next sign-in on this device (rare, sessions are
 * long-lived) asks again while the two still differ. That is on purpose: nothing about the
 * answer is stored per account, and a pick that still differs is worth one question per
 * sign-in. Picking the account's language on the device, or answering yes, ends it.
 */
export function signInLocaleDecision(
  accountApiLocale: string,
  picked: string | null | undefined,
): SignInLocaleDecision {
  const account = fromApiLocale(accountApiLocale);
  if (typeof picked !== 'string' || !isLocale(picked) || picked === account) {
    return { kind: 'useAccount', locale: account };
  }
  return { kind: 'askToSave', picked, account };
}

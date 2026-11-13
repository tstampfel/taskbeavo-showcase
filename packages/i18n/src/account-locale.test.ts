import { describe, expect, it, vi } from 'vitest';
import { createAccountLocaleSaver, signInLocaleDecision } from './account-locale';
import type { ApiLocale } from './index';

/** A save whose calls finish only when the test says so. */
function deferredSave() {
  const pending: { locale: ApiLocale; finish: () => void; fail: () => void }[] = [];
  const save = vi.fn(
    (locale: ApiLocale) =>
      new Promise<void>((resolve, reject) => {
        pending.push({ locale, finish: resolve, fail: () => reject(new Error('offline')) });
      }),
  );
  return { save, pending };
}

/** Lets queued promise callbacks run. */
async function tick() {
  for (let i = 0; i < 10; i += 1) await Promise.resolve();
}

describe('createAccountLocaleSaver', () => {
  it('saves one language at a time and ends on the last one picked', async () => {
    const saveLatest = createAccountLocaleSaver();
    const { save, pending } = deferredSave();

    const first = saveLatest('DE', save);
    await tick();
    const second = saveLatest('FR', save);
    await tick();
    // FR waits for DE instead of racing it.
    expect(pending.map((call) => call.locale)).toEqual(['DE']);
    pending[0]?.finish();
    await tick();
    expect(pending.map((call) => call.locale)).toEqual(['DE', 'FR']);
    pending[1]?.finish();
    await Promise.all([first, second]);

    expect(save.mock.calls.map(([locale]) => locale)).toEqual(['DE', 'FR']);
  });

  it('skips languages picked over while a save was in flight', async () => {
    const saveLatest = createAccountLocaleSaver();
    const { save, pending } = deferredSave();

    const de = saveLatest('DE', save);
    await tick();
    const rest = [saveLatest('EN', save), saveLatest('FR', save)];
    pending[0]?.finish();
    await tick();
    pending[1]?.finish();
    await Promise.all([de, ...rest]);

    expect(save.mock.calls.map(([locale]) => locale)).toEqual(['DE', 'FR']);
  });

  it('sends only the newest of picks made in the same moment', async () => {
    const saveLatest = createAccountLocaleSaver();
    const save = vi.fn(async () => {});

    await Promise.all([saveLatest('DE', save), saveLatest('FR', save)]);

    expect(save.mock.calls).toEqual([['FR']]);
  });

  it('sends a pick again after an earlier save of the same language went nowhere', async () => {
    const saveLatest = createAccountLocaleSaver();
    // Signed out, the app's save resolves without reaching the API (mobile) ...
    const signedOut = vi.fn(async () => {});
    // ... then the user signs in and picks the same language.
    const signedIn = vi.fn(async () => {});

    await saveLatest('FR', signedOut);
    await saveLatest('FR', signedIn);

    expect(signedIn).toHaveBeenCalledWith('FR');
  });

  it('saves the language on each account signed into in one session', async () => {
    const saveLatest = createAccountLocaleSaver();
    const accounts: Record<string, ApiLocale[]> = { ana: [], marko: [] };
    const saveTo = (account: string) => async (locale: ApiLocale) => {
      accounts[account]?.push(locale);
    };

    await saveLatest('DE', saveTo('ana'));
    await saveLatest('DE', saveTo('marko'));

    expect(accounts).toEqual({ ana: ['DE'], marko: ['DE'] });
  });

  it('retries after a failed save and never rejects', async () => {
    const saveLatest = createAccountLocaleSaver();
    const failing = vi.fn(async () => {
      throw new Error('offline');
    });
    const working = vi.fn(async () => {});

    await expect(saveLatest('DE', failing)).resolves.toBeUndefined();
    await saveLatest('DE', working);

    expect(working).toHaveBeenCalledWith('DE');
  });
});

describe('signInLocaleDecision', () => {
  it("uses the account's language when nothing was picked on this device", () => {
    expect(signInLocaleDecision('DE', null)).toEqual({ kind: 'useAccount', locale: 'de' });
    expect(signInLocaleDecision('FR', undefined)).toEqual({ kind: 'useAccount', locale: 'fr' });
  });

  it("uses the account's language when the pick is the same or unknown", () => {
    expect(signInLocaleDecision('DE', 'de')).toEqual({ kind: 'useAccount', locale: 'de' });
    expect(signInLocaleDecision('HR', 'it')).toEqual({ kind: 'useAccount', locale: 'hr' });
  });

  it('keeps a different explicit pick and asks before changing the account', () => {
    expect(signInLocaleDecision('HR', 'de')).toEqual({
      kind: 'askToSave',
      picked: 'de',
      account: 'hr',
    });
  });
});

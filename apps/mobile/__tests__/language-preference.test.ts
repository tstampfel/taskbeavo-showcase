import {
  adoptAccountLocale,
  answerLocalePrompt,
  chooseLocale,
  initialLocale,
  localeAfterSignIn,
  type LanguageDeps,
  type LocaleStore,
  type PickedLocaleStore,
} from '../src/i18n/preference';

function memoryStore(initial: string | null = null) {
  let value = initial;
  const store: LocaleStore & { value: () => string | null } = {
    load: () => value,
    save: (locale) => {
      value = locale;
    },
    value: () => value,
  };
  return store;
}

/** Waits until `ready` holds, letting promise callbacks run in between. */
async function waitFor(ready: () => boolean) {
  for (let i = 0; i < 50 && !ready(); i += 1) await Promise.resolve();
  expect(ready()).toBe(true);
}

function pickedStore(initial: string | null = null) {
  let value = initial;
  const store: PickedLocaleStore & { value: () => string | null } = {
    load: () => value,
    save: (locale) => {
      value = locale;
    },
    value: () => value,
  };
  return store;
}

function deps(store = memoryStore(), picked = pickedStore()) {
  return {
    i18n: { changeLanguage: jest.fn(async () => undefined) },
    store,
    picked,
    saveToAccount: jest.fn(async () => undefined),
  } satisfies LanguageDeps;
}

describe('initialLocale', () => {
  it('uses the language the user chose before, over the phone language', () => {
    expect(initialLocale(memoryStore('fr'), ['de-AT'])).toBe('fr');
  });

  it('uses the phone language on first launch', () => {
    expect(initialLocale(memoryStore(), ['de-AT', 'en-GB'])).toBe('de');
  });

  it('ignores a stored value it does not recognise', () => {
    expect(initialLocale(memoryStore('it'), ['en-GB'])).toBe('en');
  });

  it('falls back to the phone language when the store cannot be read', () => {
    const broken: LocaleStore = {
      load: () => {
        throw new Error('keychain locked');
      },
      save: () => {},
    };
    expect(initialLocale(broken, ['fr-CH'])).toBe('fr');
  });
});

describe('chooseLocale', () => {
  it('switches the app, remembers the choice and saves it to the account', async () => {
    const d = deps();

    await chooseLocale('de', d);

    expect(d.i18n.changeLanguage).toHaveBeenCalledWith('de');
    expect(d.store.value()).toBe('de');
    expect(d.saveToAccount).toHaveBeenCalledWith('DE');
  });

  it('keeps the choice on the phone when the account cannot be reached', async () => {
    const d = deps();
    d.saveToAccount.mockRejectedValueOnce(new Error('offline'));

    await expect(chooseLocale('fr', d)).resolves.toBeUndefined();

    expect(d.i18n.changeLanguage).toHaveBeenCalledWith('fr');
    expect(d.store.value()).toBe('fr');
  });

  it('leaves the account on the last pick when picks are made quickly', async () => {
    const d = deps();
    const finishes: (() => void)[] = [];
    d.saveToAccount.mockImplementation(
      () => new Promise<undefined>((resolve) => finishes.push(() => resolve(undefined))),
    );

    const de = chooseLocale('de', d);
    await waitFor(() => finishes.length === 1);
    const fr = chooseLocale('fr', d);
    // FR's save waits for DE's instead of racing it.
    expect(d.saveToAccount).toHaveBeenCalledTimes(1);
    finishes[0]?.();
    await waitFor(() => finishes.length === 2);
    finishes[1]?.();
    await Promise.all([de, fr]);

    expect(d.saveToAccount.mock.calls).toEqual([['DE'], ['FR']]);
    expect(d.i18n.changeLanguage).toHaveBeenLastCalledWith('fr');
    expect(d.store.value()).toBe('fr');
  });
});

describe('chooseLocale across sign-in', () => {
  it('saves a pick made signed out again when it is made signed in', async () => {
    let signedIn = false;
    const sent: string[] = [];
    const d: LanguageDeps = {
      ...deps(),
      // Like appLanguage.saveToAccount: does nothing without a session.
      async saveToAccount(locale) {
        if (signedIn) sent.push(locale);
      },
    };

    await chooseLocale('fr', d);
    signedIn = true;
    await chooseLocale('fr', d);

    expect(sent).toEqual(['FR']);
  });
});

describe('adoptAccountLocale', () => {
  it('switches to the account language after sign-in without writing it back', async () => {
    const d = deps(memoryStore('hr'));

    await adoptAccountLocale('DE', d);

    expect(d.i18n.changeLanguage).toHaveBeenCalledWith('de');
    expect(d.store.value()).toBe('de');
    expect(d.saveToAccount).not.toHaveBeenCalled();
  });
});

describe('the explicit pick', () => {
  it('is remembered by chooseLocale and forgotten when the account language is adopted', async () => {
    const d = deps();
    await chooseLocale('fr', d);
    expect(d.picked.value()).toBe('fr');
    await adoptAccountLocale('DE', d);
    expect(d.picked.value()).toBeNull();
  });
});

describe('localeAfterSignIn', () => {
  it("adopts the account's language when nothing was picked on the phone", async () => {
    const d = deps(memoryStore('en'));

    const decision = await localeAfterSignIn('DE', d);

    expect(decision).toEqual({ kind: 'useAccount', locale: 'de' });
    expect(d.i18n.changeLanguage).toHaveBeenCalledWith('de');
    expect(d.store.value()).toBe('de');
    expect(d.saveToAccount).not.toHaveBeenCalled();
  });

  it('keeps a different pick on screen and leaves the account alone until asked', async () => {
    const d = deps(memoryStore('fr'), pickedStore('fr'));

    const decision = await localeAfterSignIn('HR', d);

    expect(decision).toEqual({ kind: 'askToSave', picked: 'fr', account: 'hr' });
    expect(d.i18n.changeLanguage).not.toHaveBeenCalled();
    expect(d.store.value()).toBe('fr');
    expect(d.saveToAccount).not.toHaveBeenCalled();
  });

  it('treats an unreadable pick as none', async () => {
    const d = deps();
    d.picked.load = () => {
      throw new Error('keychain locked');
    };
    expect(await localeAfterSignIn('DE', d)).toEqual({ kind: 'useAccount', locale: 'de' });
  });
});

describe('answerLocalePrompt', () => {
  it('saves the pick on the account only on yes', async () => {
    const yes = deps();
    await answerLocalePrompt(true, 'fr', yes);
    expect(yes.saveToAccount).toHaveBeenCalledWith('FR');

    const no = deps();
    await answerLocalePrompt(false, 'fr', no);
    expect(no.saveToAccount).not.toHaveBeenCalled();
  });

  it('does not throw when the account cannot be reached', async () => {
    const d = deps();
    d.saveToAccount.mockRejectedValueOnce(new Error('offline'));
    await expect(answerLocalePrompt(true, 'de', d)).resolves.toBeUndefined();
  });
});

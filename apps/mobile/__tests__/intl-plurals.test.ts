import type { i18n as I18n } from 'i18next';
import type * as I18nModule from '../src/i18n';

jest.mock('expo-secure-store');

// Hermes on iOS has no Intl.PluralRules, so without a polyfill every ICU plural rendered as
// its raw source, e.g. "{minutes, plural, one {…} other {…}}" on checkout (BEAV-111 R1-4).
describe('ICU plurals without a native Intl.PluralRules', () => {
  const native = Intl.PluralRules;
  let i18n: I18n;

  beforeAll(() => {
    // As on Hermes: the property is missing, not just undefined.
    Reflect.deleteProperty(Intl, 'PluralRules');
    jest.isolateModules(() => {
      ({ i18n } = jest.requireActual<typeof I18nModule>('../src/i18n'));
    });
  });

  afterAll(() => {
    Object.defineProperty(Intl, 'PluralRules', { value: native, configurable: true });
  });

  it.each([
    ['en', 1, 'Checkout stays open 1 more minute'],
    ['en', 5, 'Checkout stays open 5 more minutes'],
    ['de', 1, 'Die Zahlung bleibt noch 1 Minute offen'],
  ])('formats checkout.expiresIn in %s for %i', async (locale, minutes, expected) => {
    await i18n.changeLanguage(locale);
    expect(i18n.t('checkout.expiresIn', { minutes })).toBe(expected);
  });

  it('uses the Croatian one, few and other forms', async () => {
    await i18n.changeLanguage('hr');
    expect([1, 3, 5].map((minutes) => i18n.t('checkout.expiresIn', { minutes }))).toEqual([
      'Plaćanje ostaje otvoreno još 1 minutu',
      'Plaćanje ostaje otvoreno još 3 minute',
      'Plaćanje ostaje otvoreno još 5 minuta',
    ]);
  });
});

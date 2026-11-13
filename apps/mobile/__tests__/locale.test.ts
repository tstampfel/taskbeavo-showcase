import { resolveLocale } from '../src/i18n/locale';

describe('resolveLocale', () => {
  it('picks the first supported device language', () => {
    expect(resolveLocale(['it-IT', 'de-AT', 'en-GB'])).toBe('de');
  });

  it('matches on the language code regardless of region or case', () => {
    expect(resolveLocale(['FR-ch'])).toBe('fr');
    expect(resolveLocale(['hr'])).toBe('hr');
  });

  it('falls back to Croatian when nothing is supported', () => {
    expect(resolveLocale(['it-IT', 'es'])).toBe('hr');
    expect(resolveLocale([])).toBe('hr');
  });
});

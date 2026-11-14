import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  countryCodes,
  countryFlag,
  countryName,
  defaultCountry,
  featuredCountryCodes,
  phonePrefix,
  sortedCountries,
} from './countries';

describe('countries', () => {
  it('lists each ISO country once, featured ones included', () => {
    expect(new Set(countryCodes).size).toBe(countryCodes.length);
    expect(countryCodes.length).toBeGreaterThan(240);
    for (const code of featuredCountryCodes) expect(countryCodes).toContain(code);
  });

  it('names countries in the app language', () => {
    expect(countryName('DE', 'de')).toBe('Deutschland');
    expect(countryName('DE', 'hr')).toBe('Njemačka');
    expect(countryName('HR', 'fr')).toBe('Croatie');
  });

  describe('without Intl.DisplayNames, as on Hermes (BEAV-223)', () => {
    afterEach(() => vi.unstubAllGlobals());

    it('still names every country in hr, en, de and fr, never by its code', () => {
      vi.stubGlobal('Intl', { ...Intl, DisplayNames: undefined });
      expect(countryName('DE', 'de')).toBe('Deutschland');
      expect(countryName('AT', 'hr')).toBe('Austrija');
      expect(countryName('CH', 'fr')).toBe('Suisse');
      for (const locale of ['hr', 'en', 'de', 'fr'] as const) {
        for (const { code, name } of sortedCountries(locale)) {
          expect({ code, locale, named: name !== code && name.length > 2 }).toEqual({
            code,
            locale,
            named: true,
          });
        }
      }
    });
  });

  it('puts the featured countries first, then the rest by name', () => {
    const list = sortedCountries('en');
    expect(list.slice(0, 6).map((c) => c.code)).toEqual(['HR', 'DE', 'AT', 'CH', 'FR', 'BE']);
    expect(list).toHaveLength(countryCodes.length);
    const rest = list.slice(6).map((c) => c.name);
    expect(rest).toEqual([...rest].sort((a, b) => a.localeCompare(b, 'en')));
  });

  it('builds the flag from the code', () => {
    expect(countryFlag('DE')).toBe('🇩🇪');
    expect(countryFlag('de')).toBe('');
  });

  it('starts the phone number with the country prefix', () => {
    expect(phonePrefix('DE')).toBe('+49 ');
    expect(phonePrefix('hr')).toBe('+385 ');
    expect(phonePrefix('KI')).toBe('+');
  });

  it('pre-selects the country of the first language tag with a region', () => {
    expect(defaultCountry(['de', 'de-AT', 'en-US'])).toBe('AT');
    expect(defaultCountry(['fr_CH'])).toBe('CH');
    expect(defaultCountry(['en'])).toBe('HR');
    expect(defaultCountry([])).toBe('HR');
  });
});

import { describe, expect, it } from 'vitest';
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

import {
  isPluralElement,
  isTagElement,
  parse,
  type MessageFormatElement,
} from '@formatjs/icu-messageformat-parser';
import { IntlMessageFormat } from 'intl-messageformat';
import { describe, expect, it } from 'vitest';
import {
  catalogues,
  defaultLocale,
  fromApiLocale,
  isLocale,
  languageNames,
  locales,
  sourceLocale,
  toApiLocale,
  type Locale,
} from './index';

/** Flattens a nested catalogue to `[dotted.key, message]` pairs. */
function entriesOf(obj: object, prefix = ''): [string, unknown][] {
  return Object.entries(obj).flatMap(([key, value]) =>
    typeof value === 'object' && value !== null
      ? entriesOf(value as object, `${prefix}${key}.`)
      : [[`${prefix}${key}`, value] as [string, unknown]],
  );
}

/**
 * The option keys of every cardinal plural in a parsed message, including plurals nested in
 * other arguments or rich-text tags. Ordinals (`selectordinal`) have their own categories
 * (Croatian has only `other`), so they're not held to the cardinal ones.
 */
function pluralsIn(elements: MessageFormatElement[]): string[][] {
  return elements.flatMap((element) => {
    if (isTagElement(element)) return pluralsIn(element.children);
    if (!('options' in element)) return [];
    const nested = Object.values(element.options).flatMap((option) => pluralsIn(option.value));
    return isPluralElement(element) && element.pluralType === 'cardinal'
      ? [Object.keys(element.options), ...nested]
      : nested;
  });
}

/** The plural categories each language needs; Croatian has three. */
const pluralCategories: Record<Locale, string[]> = {
  hr: ['one', 'few', 'other'],
  en: ['one', 'other'],
  de: ['one', 'other'],
  fr: ['one', 'other'],
};

describe('locales', () => {
  it('are hr, en, de and fr, with hr as default and en as source', () => {
    expect(locales).toEqual(['hr', 'en', 'de', 'fr']);
    expect(defaultLocale).toBe('hr');
    expect(sourceLocale).toBe('en');
  });

  it('isLocale accepts only supported locales', () => {
    expect(isLocale('de')).toBe(true);
    expect(isLocale('it')).toBe(false);
  });

  it('map to and from the API Locale enum', () => {
    expect(locales.map(toApiLocale)).toEqual(['HR', 'EN', 'DE', 'FR']);
    expect(fromApiLocale('DE')).toBe('de');
    expect(fromApiLocale('fr')).toBe('fr');
    expect(fromApiLocale('IT')).toBe('hr');
  });

  it('name each language in itself, for the switcher', () => {
    expect(languageNames).toEqual({
      hr: 'Hrvatski',
      en: 'English',
      de: 'Deutsch',
      fr: 'Français',
    });
  });
});

describe('catalogues', () => {
  const sourceKeys = entriesOf(catalogues[sourceLocale])
    .map(([key]) => key)
    .sort();

  it.each(locales)('%s has exactly the same keys as en', (locale) => {
    const keys = entriesOf(catalogues[locale]).map(([key]) => key);
    expect(keys.sort()).toEqual(sourceKeys);
  });

  it.each(locales)('%s messages are non-empty, valid ICU', (locale) => {
    for (const [key, message] of entriesOf(catalogues[locale])) {
      expect(message, key).toBeTypeOf('string');
      expect(message as string, key).toMatch(/\S/);
      expect(() => parse(message as string), key).not.toThrow();
    }
  });
});

describe('plurals', () => {
  it('finds cardinal plurals inside tags and ignores ordinals', () => {
    expect(pluralsIn(parse('<b>{n, plural, one {#} few {#} other {#}}</b>'))).toEqual([
      ['one', 'few', 'other'],
    ]);
    expect(pluralsIn(parse('{n, selectordinal, other {#.}}'))).toEqual([]);
  });

  it.each(locales)('%s plurals cover every category the language needs', (locale) => {
    for (const [key, message] of entriesOf(catalogues[locale])) {
      for (const options of pluralsIn(parse(message as string))) {
        expect(options, key).toEqual(expect.arrayContaining(pluralCategories[locale]));
      }
    }
  });

  it('Croatian picks one, few or other by the number', () => {
    const offers = new IntlMessageFormat(catalogues.hr.offers.count, 'hr');
    const counts = [1, 2, 4, 5, 11, 21, 22, 25];
    expect(counts.map((count) => offers.format({ count }))).toEqual([
      '1 ponuda',
      '2 ponude',
      '4 ponude',
      '5 ponuda',
      '11 ponuda',
      '21 ponuda',
      '22 ponude',
      '25 ponuda',
    ]);
  });

  it('French treats 0 as one', () => {
    const offers = new IntlMessageFormat(catalogues.fr.offers.count, 'fr');
    expect(offers.format({ count: 0 })).toBe('0 offre');
    expect(offers.format({ count: 2 })).toBe('2 offres');
  });
});

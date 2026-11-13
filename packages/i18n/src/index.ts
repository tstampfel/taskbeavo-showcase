import de from './locales/de.json';
import en from './locales/en.json';
import fr from './locales/fr.json';
import hr from './locales/hr.json';

export const locales = ['hr', 'en', 'de', 'fr'] as const;
export type Locale = (typeof locales)[number];

/** Croatian is the default for helpers and on-site contacts. */
export const defaultLocale: Locale = 'hr';
/** English is the source language translators work from. */
export const sourceLocale: Locale = 'en';

export type Messages = typeof en;

export const catalogues: Record<Locale, Messages> = { hr, en, de, fr };

export function isLocale(value: string): value is Locale {
  return (locales as readonly string[]).includes(value);
}

/** The GraphQL `Locale` enum value, e.g. "DE". */
export type ApiLocale = Uppercase<Locale>;

export function toApiLocale(locale: Locale): ApiLocale {
  return locale.toUpperCase() as ApiLocale;
}

/** A locale from the API (e.g. `viewer.locale`); Croatian when it isn't one we support. */
export function fromApiLocale(value: string): Locale {
  const locale = value.toLowerCase();
  return isLocale(locale) ? locale : defaultLocale;
}

/** Each language named in itself, as language switchers show it. Not translated. */
export const languageNames: Record<Locale, string> = {
  hr: 'Hrvatski',
  en: 'English',
  de: 'Deutsch',
  fr: 'Français',
};

export * from './account-locale';
export * from './format';
export * from './countries';
export * from './geolite';

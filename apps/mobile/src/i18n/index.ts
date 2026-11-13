import './intl-polyfills';
import { catalogues, defaultLocale, locales } from '@taskbeavo/i18n';
import { getLocales } from 'expo-localization';
import i18next from 'i18next';
import ICU from 'i18next-icu';
import { initReactI18next } from 'react-i18next';
import { secureLocaleStore } from './locale-store';
import { initialLocale } from './preference';

/**
 * The language the user picked on this phone, else the phone's language. The switcher saves a
 * new choice on the phone and the account (`chooseLocale`). After sign-in the account's saved
 * language takes over, unless the user picked another one here (`localeAfterSignIn`).
 */
export const i18n = i18next.createInstance();

void i18n
  .use(ICU)
  .use(initReactI18next)
  .init({
    lng: initialLocale(
      secureLocaleStore,
      getLocales().map((locale) => locale.languageTag),
    ),
    fallbackLng: defaultLocale,
    supportedLngs: [...locales],
    resources: Object.fromEntries(
      locales.map((locale) => [locale, { translation: catalogues[locale] }]),
    ),
    interpolation: { escapeValue: false },
    initAsync: false,
  });

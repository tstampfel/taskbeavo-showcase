import { defaultLocale, locales } from '@taskbeavo/i18n';
import { defineRouting } from 'next-intl/routing';

/** Remembers a language the user picked for a year, so `/` sends them back to it. */
export const LOCALE_COOKIE = 'NEXT_LOCALE';
export const LOCALE_COOKIE_MAX_AGE = 60 * 60 * 24 * 365;

/**
 * Every page lives under /hr, /en, /de or /fr; `/` redirects by the locale cookie, else
 * Accept-Language. Only an active pick writes the cookie: the language switcher (next-intl's
 * Link with `locale`) or signing in (the account's saved language). Just visiting a page of a
 * locale never does; proxy.ts drops the middleware's write.
 */
export const routing = defineRouting({
  locales,
  defaultLocale,
  localePrefix: 'always',
  localeCookie: { name: LOCALE_COOKIE, maxAge: LOCALE_COOKIE_MAX_AGE },
});

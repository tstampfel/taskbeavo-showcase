import { fromApiLocale } from '@taskbeavo/i18n';
import type { CookieWrite } from '../auth/session';
import { LOCALE_COOKIE, LOCALE_COOKIE_MAX_AGE } from './routing';

/**
 * The locale cookie for an account's saved language (`User.locale`, e.g. "DE"), set at sign-in
 * so `/` redirects to it. Besides this, only the language switcher writes the cookie (next-intl's
 * Link with `locale` sets it in the browser); the middleware never does (`withoutLocaleCookieWrite`).
 */
export function accountLocaleCookie(apiLocale: string): CookieWrite {
  return {
    name: LOCALE_COOKIE,
    value: fromApiLocale(apiLocale),
    options: { sameSite: 'lax', path: '/', maxAge: LOCALE_COOKIE_MAX_AGE },
  };
}

/**
 * Removes the locale cookie next-intl's middleware sets when a page of another language is
 * opened (an Accept-Language redirect, a shared link). Only an active pick may create it, so
 * the cookie stays strictly necessary and needs no consent (ZEK čl. 43(4)). Other cookies stay.
 */
export function withoutLocaleCookieWrite<R extends Response>(response: R): R {
  const setCookies = response.headers.getSetCookie();
  const kept = setCookies.filter((cookie) => !cookie.startsWith(`${LOCALE_COOKIE}=`));
  if (kept.length === setCookies.length) return response;
  response.headers.delete('set-cookie');
  for (const cookie of kept) response.headers.append('set-cookie', cookie);
  return response;
}

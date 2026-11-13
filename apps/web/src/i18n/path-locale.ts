import { routing } from './routing';

/**
 * The locale from the path, for global-error, which replaces the [locale] layout and its next-intl
 * provider. `usePathname` gives the same answer on the server and in the browser.
 */
export function pathLocale(pathname: string | null): (typeof routing.locales)[number] {
  const segment = pathname?.split('/')[1];
  return routing.locales.find((locale) => locale === segment) ?? routing.defaultLocale;
}

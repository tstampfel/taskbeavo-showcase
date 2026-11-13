import type { Locale } from '@taskbeavo/i18n';
import type { Metadata } from 'next';
import { routing } from './routing';

/** Absolute base for canonical and hreflang URLs: an explicit site URL, else Vercel's production domain. */
export function siteUrl(): URL {
  const explicit = process.env.NEXT_PUBLIC_SITE_URL;
  if (explicit) return new URL(explicit);
  const vercel = process.env.VERCEL_PROJECT_PRODUCTION_URL;
  if (vercel) return new URL(`https://${vercel}`);
  return new URL(`http://localhost:${process.env.PORT ?? 3000}`);
}

/** Canonical plus hreflang links for one page; `path` is the route after the locale, e.g. '' or '/tasks'. */
export function localeAlternates(locale: Locale, path = ''): Metadata['alternates'] {
  return {
    canonical: `/${locale}${path}`,
    languages: {
      ...Object.fromEntries(routing.locales.map((l) => [l, `/${l}${path}`])),
      'x-default': `/${routing.defaultLocale}${path}`,
    },
  };
}

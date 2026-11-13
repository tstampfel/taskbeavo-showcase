import { defaultLocale, isLocale, type Locale } from '@taskbeavo/i18n';

/** First device language TaskBeavo supports, matched on the language code; Croatian otherwise. */
export function resolveLocale(deviceLanguageTags: readonly string[]): Locale {
  for (const tag of deviceLanguageTags) {
    const language = tag.split('-')[0]?.toLowerCase() ?? '';
    if (isLocale(language)) return language;
  }
  return defaultLocale;
}

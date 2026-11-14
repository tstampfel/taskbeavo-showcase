import type { AbstractIntlMessages } from 'next-intl';

/**
 * The namespaces the locale layout ships to the browser for the headers (AppShell, SiteHeader),
 * the banners and the error screen. A page's own NextIntlClientProvider replaces these rather
 * than adding to them, so a page that puts the AppShell inside its provider passes them on too.
 */
export const SHELL_NAMESPACES = [
  'apiStatus',
  'appShell',
  'consent',
  'errorBoundary',
  'languageSwitcher',
  'sessions',
  'settings',
  'signIn',
  'termsUpdate',
] as const;

/** Only `namespaces` of `messages`, so the rest of the catalogue stays on the server. */
export function pickMessages(
  messages: AbstractIntlMessages,
  namespaces: readonly string[],
): AbstractIntlMessages {
  const picked: AbstractIntlMessages = {};
  for (const namespace of namespaces) {
    const value = messages[namespace];
    if (value !== undefined) picked[namespace] = value;
  }
  return picked;
}

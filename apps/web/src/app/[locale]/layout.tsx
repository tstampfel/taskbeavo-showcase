import { colors, cssVariables } from '@taskbeavo/ui-tokens';
import type { Metadata } from 'next';
import { Manrope } from 'next/font/google';
import { notFound } from 'next/navigation';
import { hasLocale, NextIntlClientProvider } from 'next-intl';
import { getMessages, getTranslations, setRequestLocale } from 'next-intl/server';
import { ApolloProvider } from '../../apollo/apollo-provider';
import { LegalFooter } from '../../components/legal/legal-document-view';
import { pickMessages, SHELL_NAMESPACES } from '../../i18n/client-messages';
import { siteUrl } from '../../i18n/metadata';
import { routing } from '../../i18n/routing';

const manrope = Manrope({ subsets: ['latin', 'latin-ext'], display: 'swap' });

// Only the four locales exist; anything else (e.g. /robots.txt, which the proxy skips) falls to global-not-found.
export const dynamicParams = false;

export function generateStaticParams() {
  return routing.locales.map((locale) => ({ locale }));
}

/** Site-wide defaults only; each page sets its own canonical and hreflang via localeAlternates. */
export async function generateMetadata({ params }: LayoutProps<'/[locale]'>): Promise<Metadata> {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) notFound();
  const t = await getTranslations({ locale, namespace: 'common' });
  return {
    metadataBase: siteUrl(),
    title: { default: t('appName'), template: `%s · ${t('appName')}` },
    description: t('tagline'),
  };
}

export default async function LocaleLayout({ children, params }: LayoutProps<'/[locale]'>) {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) notFound();
  setRequestLocale(locale);

  // Only the namespaces client components use ship to the browser.
  const messages = pickMessages(await getMessages(), SHELL_NAMESPACES);
  return (
    <html lang={locale}>
      <body
        className={manrope.className}
        // The --tb-* variables feed the base components in components/ui.
        style={{ ...cssVariables(), margin: 0, background: colors.offWhite, color: colors.ink }}
      >
        <NextIntlClientProvider messages={messages}>
          <ApolloProvider>
            {/* Each page brings its header: AppShell when logged in, SiteHeader when public. */}
            {children}
            <LegalFooter locale={locale} />
          </ApolloProvider>
        </NextIntlClientProvider>
      </body>
    </html>
  );
}

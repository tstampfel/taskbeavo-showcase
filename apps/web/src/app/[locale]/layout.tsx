import { colors, cssVariables } from '@taskbeavo/ui-tokens';
import type { Metadata } from 'next';
import { Manrope } from 'next/font/google';
import { notFound } from 'next/navigation';
import { hasLocale, NextIntlClientProvider } from 'next-intl';
import { getMessages, getTranslations, setRequestLocale } from 'next-intl/server';
import { ApolloProvider } from '../../apollo/apollo-provider';
import { AccountMenu } from '../../components/account-menu';
import { ConsentBanner } from '../../components/consent/consent-banner';
import { LanguageSwitcher } from '../../components/language-switcher';
import { LegalFooter } from '../../components/legal/legal-document-view';
import { TermsBanner } from '../../components/terms-update/terms-banner';
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
  const {
    apiStatus,
    consent,
    errorBoundary,
    languageSwitcher,
    sessions,
    settings,
    signIn,
    termsUpdate,
  } = await getMessages();
  return (
    <html lang={locale}>
      <body
        className={manrope.className}
        // The --tb-* variables feed the base components in components/ui.
        style={{ ...cssVariables(), margin: 0, background: colors.offWhite, color: colors.ink }}
      >
        <NextIntlClientProvider
          messages={{
            apiStatus,
            consent,
            errorBoundary,
            languageSwitcher,
            sessions,
            settings,
            signIn,
            termsUpdate,
          }}
        >
          <ApolloProvider>
            <header
              style={{
                display: 'flex',
                flexWrap: 'wrap',
                justifyContent: 'flex-end',
                alignItems: 'center',
                gap: 12,
                padding: '12px 16px 0',
              }}
            >
              <AccountMenu />
              <LanguageSwitcher />
            </header>
            <TermsBanner />
            <ConsentBanner />
            {children}
            <LegalFooter locale={locale} />
          </ApolloProvider>
        </NextIntlClientProvider>
      </body>
    </html>
  );
}

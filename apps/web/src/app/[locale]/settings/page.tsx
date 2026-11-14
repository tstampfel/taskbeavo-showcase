import type { Metadata } from 'next';
import { hasLocale, NextIntlClientProvider } from 'next-intl';
import { getMessages, getTranslations, setRequestLocale } from 'next-intl/server';
import { notFound } from 'next/navigation';
import { BusinessDetails } from '../../../components/settings/business-details';
import { PrivacySettings } from '../../../components/settings/privacy-settings';
import styles from '../../../components/settings/settings.module.css';
import { routing } from '../../../i18n/routing';
import { AppShell } from '../../../components/shell/app-shell';

export async function generateMetadata({
  params,
}: PageProps<'/[locale]/settings'>): Promise<Metadata> {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) return {};
  const t = await getTranslations({ locale, namespace: 'settings' });
  return { title: t('title'), robots: { index: false, follow: false } };
}

/**
 * Account settings: Business details (BEAV-199), the obrt or company a poster books for, and
 * Privacy and data (BEAV-83), the analytics and marketing consent switches. One column at
 * every width (mockups 14c and 19a; BEAV-213).
 */
export default async function SettingsPage({ params }: PageProps<'/[locale]/settings'>) {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) notFound();
  setRequestLocale(locale);
  const t = await getTranslations({ locale, namespace: 'settings' });
  const { businessDetails, signIn, errors, errorBoundary, legal, privacySettings } =
    await getMessages();

  return (
    <AppShell title={t('title')}>
      <main className={styles.main}>
        <NextIntlClientProvider
          messages={{ businessDetails, signIn, errors, errorBoundary, legal, privacySettings }}
        >
          <BusinessDetails />
          <PrivacySettings />
        </NextIntlClientProvider>
      </main>
    </AppShell>
  );
}

import type { Metadata } from 'next';
import { hasLocale, NextIntlClientProvider } from 'next-intl';
import { getMessages, getTranslations, setRequestLocale } from 'next-intl/server';
import { notFound } from 'next/navigation';
import { BusinessDetails } from '../../../components/settings/business-details';
import styles from '../../../components/sign-in/sign-in.module.css';
import { routing } from '../../../i18n/routing';

export async function generateMetadata({
  params,
}: PageProps<'/[locale]/settings'>): Promise<Metadata> {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) return {};
  const t = await getTranslations({ locale, namespace: 'settings' });
  return { title: t('title'), robots: { index: false, follow: false } };
}

/** Account settings, for now Business details (BEAV-199): the obrt or company a poster books for. */
export default async function SettingsPage({ params }: PageProps<'/[locale]/settings'>) {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) notFound();
  setRequestLocale(locale);
  const { businessDetails, signIn, errors, errorBoundary } = await getMessages();

  return (
    <main className={styles.main}>
      <NextIntlClientProvider messages={{ businessDetails, signIn, errors, errorBoundary }}>
        <BusinessDetails />
      </NextIntlClientProvider>
    </main>
  );
}

import type { Metadata } from 'next';
import { hasLocale, NextIntlClientProvider } from 'next-intl';
import { getMessages, getTranslations, setRequestLocale } from 'next-intl/server';
import { notFound } from 'next/navigation';
import styles from '../../../components/sign-in/sign-in.module.css';
import { ThisWasntMe } from '../../../components/sign-in/this-wasnt-me';
import { routing } from '../../../i18n/routing';

export async function generateMetadata({
  params,
}: PageProps<'/[locale]/this-wasnt-me'>): Promise<Metadata> {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) return {};
  const t = await getTranslations({ locale, namespace: 'sessions' });
  // The token is in the URL: no indexing, and no referrer to other sites.
  return {
    title: t('notMeMetaTitle'),
    robots: { index: false, follow: false },
    referrer: 'no-referrer',
  };
}

/**
 * "This wasn't me", from the new-device email. Nothing happens on opening the page (mail
 * scanners open links); the person confirms with the button, which logs the account out of
 * all devices without signing in.
 */
export default async function ThisWasntMePage({
  params,
  searchParams,
}: PageProps<'/[locale]/this-wasnt-me'>) {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) notFound();
  setRequestLocale(locale);
  const { token } = await searchParams;
  const { sessions, signIn, errors, errorBoundary } = await getMessages();

  return (
    <main className={styles.main}>
      <NextIntlClientProvider messages={{ sessions, signIn, errors, errorBoundary }}>
        <ThisWasntMe token={typeof token === 'string' && token ? token : null} />
      </NextIntlClientProvider>
    </main>
  );
}

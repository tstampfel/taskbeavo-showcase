import type { Metadata } from 'next';
import { hasLocale, NextIntlClientProvider } from 'next-intl';
import { getMessages, getTranslations, setRequestLocale } from 'next-intl/server';
import { notFound } from 'next/navigation';
import styles from '../../../components/sign-in/sign-in.module.css';
import { TermsUpdate } from '../../../components/terms-update/terms-update';
import { routing } from '../../../i18n/routing';

export async function generateMetadata({
  params,
}: PageProps<'/[locale]/terms-update'>): Promise<Metadata> {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) return {};
  const t = await getTranslations({ locale, namespace: 'termsUpdate' });
  return { title: t('metaTitle'), robots: { index: false, follow: false } };
}

/** We've updated our terms (mockup 21b): the way out of limited mode after a terms change. */
export default async function TermsUpdatePage({
  params,
  searchParams,
}: PageProps<'/[locale]/terms-update'>) {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) notFound();
  setRequestLocale(locale);
  const { next } = await searchParams;
  const { termsUpdate, signIn, errors } = await getMessages();

  return (
    <main className={styles.main}>
      <NextIntlClientProvider messages={{ termsUpdate, signIn, errors }}>
        <TermsUpdate next={typeof next === 'string' ? next : null} />
      </NextIntlClientProvider>
    </main>
  );
}

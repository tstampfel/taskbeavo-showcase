import type { Metadata } from 'next';
import { hasLocale, NextIntlClientProvider } from 'next-intl';
import { getMessages, getTranslations, setRequestLocale } from 'next-intl/server';
import { notFound } from 'next/navigation';
import { PhoneVerification } from '../../../components/sign-in/phone-verification';
import styles from '../../../components/sign-in/sign-in.module.css';
import { routing } from '../../../i18n/routing';
import { AppShell } from '../../../components/shell/app-shell';

export async function generateMetadata({
  params,
}: PageProps<'/[locale]/verify-phone'>): Promise<Metadata> {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) return {};
  const t = await getTranslations({ locale, namespace: 'phone' });
  return { title: t('metaTitle'), robots: { index: false, follow: false } };
}

/** Verify your phone (mockups 15f and 15g), right after sign-up and whenever it's needed. */
export default async function VerifyPhonePage({
  params,
  searchParams,
}: PageProps<'/[locale]/verify-phone'>) {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) notFound();
  setRequestLocale(locale);
  const t = await getTranslations({ locale, namespace: 'phone' });
  const { next } = await searchParams;
  const { signIn, phone, errors, errorBoundary } = await getMessages();

  return (
    <AppShell title={t('metaTitle')} titleIsHeading={false}>
      <main className={styles.main}>
        <NextIntlClientProvider messages={{ signIn, phone, errors, errorBoundary }}>
          <PhoneVerification next={typeof next === 'string' ? next : null} />
        </NextIntlClientProvider>
      </main>
    </AppShell>
  );
}

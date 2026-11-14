import type { Metadata } from 'next';
import { hasLocale, NextIntlClientProvider } from 'next-intl';
import { getMessages, getTranslations, setRequestLocale } from 'next-intl/server';
import { notFound } from 'next/navigation';
import { SiteHeader } from '../../../components/shell/site-header';
import { SignInFlow } from '../../../components/sign-in/sign-in-flow';
import styles from '../../../components/sign-in/sign-in.module.css';
import { routing } from '../../../i18n/routing';

export async function generateMetadata({
  params,
}: PageProps<'/[locale]/sign-in'>): Promise<Metadata> {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) return {};
  const t = await getTranslations({ locale, namespace: 'signIn' });
  return { title: t('metaTitle'), robots: { index: false, follow: false } };
}

/** Log in or sign up (mockup 15j). */
export default async function SignInPage({ params, searchParams }: PageProps<'/[locale]/sign-in'>) {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) notFound();
  setRequestLocale(locale);
  const t = await getTranslations({ locale, namespace: 'signIn' });
  const { next } = await searchParams;
  const { signIn, errors } = await getMessages();

  return (
    <>
      <SiteHeader />
      <div className={styles.page}>
        <aside className={styles.hero}>
          <p className={styles.heroTitle}>{t('heroTitle')}</p>
          <p className={styles.heroBody}>{t('heroBody')}</p>
        </aside>
        <main className={styles.main}>
          <NextIntlClientProvider messages={{ signIn, errors }}>
            <SignInFlow next={typeof next === 'string' ? next : null} />
          </NextIntlClientProvider>
        </main>
      </div>
    </>
  );
}

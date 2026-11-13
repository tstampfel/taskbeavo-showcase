import { colors } from '@taskbeavo/ui-tokens';
import type { Metadata } from 'next';
import { hasLocale, useTranslations } from 'next-intl';
import { setRequestLocale } from 'next-intl/server';
import { use } from 'react';
import { ApiStatus } from '../../components/api-status';
import { localeAlternates } from '../../i18n/metadata';
import { routing } from '../../i18n/routing';

export async function generateMetadata({ params }: PageProps<'/[locale]'>): Promise<Metadata> {
  const { locale } = await params;
  return hasLocale(routing.locales, locale) ? { alternates: localeAlternates(locale) } : {};
}

/** Placeholder home page until the landing page is designed. */
export default function HomePage({ params }: PageProps<'/[locale]'>) {
  const { locale } = use(params);
  setRequestLocale(locale);
  const t = useTranslations();

  return (
    <main
      data-testid="home"
      style={{ maxWidth: 720, margin: '0 auto', padding: '64px 16px', boxSizing: 'border-box' }}
    >
      <h1 style={{ color: colors.green, fontSize: '2.5rem', margin: 0 }}>{t('common.appName')}</h1>
      <p data-testid="home-tagline" style={{ fontSize: '1.25rem', fontWeight: 600 }}>
        {t('common.tagline')}
      </p>
      <p>{t('home.comingSoon')}</p>
      <ApiStatus />
    </main>
  );
}

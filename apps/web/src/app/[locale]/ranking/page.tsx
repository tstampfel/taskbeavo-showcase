import { isLocale, type Locale } from '@taskbeavo/i18n';
import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { getTranslations, setRequestLocale } from 'next-intl/server';
import { RankingView } from '../../../components/ranking/ranking-view';
import { localeAlternates } from '../../../i18n/metadata';

function checked(locale: string): Locale {
  if (!isLocale(locale)) notFound();
  return locale;
}

export async function generateMetadata({
  params,
}: PageProps<'/[locale]/ranking'>): Promise<Metadata> {
  const locale = checked((await params).locale);
  const t = await getTranslations({ locale, namespace: 'ranking' });
  return {
    title: t('title'),
    description: t('metaDescription'),
    alternates: localeAlternates(locale, '/ranking'),
    // DRAFT wording stays out of search results until the lawyer approves it.
    robots: { index: false, follow: true },
  };
}

/** /{locale}/ranking: how the offers on a task are ordered (BEAV-191). */
export default async function RankingPage({ params }: PageProps<'/[locale]/ranking'>) {
  const locale = checked((await params).locale);
  setRequestLocale(locale);
  return <RankingView locale={locale} />;
}

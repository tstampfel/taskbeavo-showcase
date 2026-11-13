import { isLocale, type Locale } from '@taskbeavo/i18n';
import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { getTranslations, setRequestLocale } from 'next-intl/server';
import { loadFees } from '../../../../components/fees/fee-schedules';
import { FeesArchiveView } from '../../../../components/fees/fees-view';
import { localeAlternates } from '../../../../i18n/metadata';

function checked(locale: string): Locale {
  if (!isLocale(locale)) notFound();
  return locale;
}

export async function generateMetadata({
  params,
}: PageProps<'/[locale]/fees/archive'>): Promise<Metadata> {
  const locale = checked((await params).locale);
  const t = await getTranslations({ locale, namespace: 'fees' });
  return {
    title: t('archive.title'),
    description: t('archive.metaDescription'),
    alternates: localeAlternates(locale, '/fees/archive'),
    robots: { index: false, follow: true },
  };
}

/** /{locale}/fees/archive: the price list in force, then every earlier one. */
export default async function FeesArchivePage({ params }: PageProps<'/[locale]/fees/archive'>) {
  const locale = checked((await params).locale);
  setRequestLocale(locale);
  // Announced lists are on /fees until they apply.
  const schedules = (await loadFees()).schedules.filter(
    (schedule) => schedule.status !== 'UPCOMING',
  );
  return <FeesArchiveView schedules={schedules} locale={locale} />;
}

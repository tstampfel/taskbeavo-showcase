import { isLocale, type Locale } from '@taskbeavo/i18n';
import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { getTranslations, setRequestLocale } from 'next-intl/server';
import { currentSchedule, loadFees } from '../../../components/fees/fee-schedules';
import { FeesView } from '../../../components/fees/fees-view';
import { localeAlternates } from '../../../i18n/metadata';

function checked(locale: string): Locale {
  if (!isLocale(locale)) notFound();
  return locale;
}

export async function generateMetadata({ params }: PageProps<'/[locale]/fees'>): Promise<Metadata> {
  const locale = checked((await params).locale);
  const t = await getTranslations({ locale, namespace: 'fees' });
  return {
    title: t('title'),
    description: t('metaDescription'),
    alternates: localeAlternates(locale, '/fees'),
    // DRAFT wording stays out of search results until the lawyer approves it.
    robots: { index: false, follow: true },
  };
}

/** /{locale}/fees: the service fee price list in force (BEAV-191, ZZP čl. 7(9)). */
export default async function FeesPage({ params }: PageProps<'/[locale]/fees'>) {
  const locale = checked((await params).locale);
  setRequestLocale(locale);
  const { schedules, cancellation } = await loadFees();
  return (
    <FeesView
      schedule={currentSchedule(schedules)}
      upcoming={schedules.filter((schedule) => schedule.status === 'UPCOMING')}
      cancellation={cancellation}
      locale={locale}
    />
  );
}

import { LINK_ID } from '@taskbeavo/app-links';
import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { hasLocale, NextIntlClientProvider } from 'next-intl';
import { getMessages, getTranslations, setRequestLocale } from 'next-intl/server';
import styles from '../../../../../components/arrival/arrival.module.css';
import { BookingArrival } from '../../../../../components/arrival/booking-arrival';
import { routing } from '../../../../../i18n/routing';
import { AppShell } from '../../../../../components/shell/app-shell';

type Props = PageProps<'/[locale]/bookings/[id]/arrival'>;

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) return {};
  const t = await getTranslations({ locale, namespace: 'arrival' });
  return { title: t('metaTitle'), robots: { index: false, follow: false } };
}

/** The day of the job for both sides (mockup 18); arrival pushes and emails open it. */
export default async function BookingArrivalPage({ params }: Props) {
  const { locale, id } = await params;
  if (!hasLocale(routing.locales, locale) || !LINK_ID.test(id)) notFound();
  setRequestLocale(locale);
  const { arrival, errors } = await getMessages();
  const t = await getTranslations({ locale, namespace: 'arrival' });
  return (
    <AppShell title={t('title')} back={`/bookings/${id}`}>
      <main className={styles.main}>
        <NextIntlClientProvider messages={{ arrival, errors }}>
          <BookingArrival bookingId={id} locale={locale} />
        </NextIntlClientProvider>
      </main>
    </AppShell>
  );
}

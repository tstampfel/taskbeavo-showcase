import { LINK_ID } from '@taskbeavo/app-links';
import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { hasLocale, NextIntlClientProvider } from 'next-intl';
import { getMessages, getTranslations, setRequestLocale } from 'next-intl/server';
import { BookingDetailView } from '../../../../components/booking/booking-detail';
import styles from '../../../../components/booking/booking.module.css';
import { routing } from '../../../../i18n/routing';

type Props = PageProps<'/[locale]/bookings/[id]'>;

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) return {};
  const t = await getTranslations({ locale, namespace: 'bookingDetail' });
  return { title: t('metaTitle'), robots: { index: false, follow: false } };
}

/** Booking detail for both sides (mockup 05); booking emails and pushes open it. */
export default async function BookingDetailPage({ params }: Props) {
  const { locale, id } = await params;
  if (!hasLocale(routing.locales, locale) || !LINK_ID.test(id)) notFound();
  setRequestLocale(locale);
  const { bookingDetail, checkout, offers, errors } = await getMessages();
  const t = await getTranslations({ locale, namespace: 'bookingDetail' });
  return (
    <main className={styles.main}>
      <h1 className={styles.title}>{t('metaTitle')}</h1>
      <NextIntlClientProvider messages={{ bookingDetail, checkout, offers, errors }}>
        <BookingDetailView bookingId={id} locale={locale} />
      </NextIntlClientProvider>
    </main>
  );
}

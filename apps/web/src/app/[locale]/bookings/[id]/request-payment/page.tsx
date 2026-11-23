import { LINK_ID } from '@taskbeavo/app-links';
import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { hasLocale, NextIntlClientProvider } from 'next-intl';
import { getMessages, getTranslations, setRequestLocale } from 'next-intl/server';
import { BookingRequestPayment } from '../../../../../components/release/booking-request-payment';
import styles from '../../../../../components/release/release.module.css';
import { AppShell } from '../../../../../components/shell/app-shell';
import { routing } from '../../../../../i18n/routing';

type Props = PageProps<'/[locale]/bookings/[id]/request-payment'>;

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) return {};
  const t = await getTranslations({ locale, namespace: 'requestPayment' });
  return { title: t('title'), robots: { index: false, follow: false } };
}

/** The helper's Request payment (mockup 7a): a note and, for a business, its invoice. */
export default async function BookingRequestPaymentPage({ params }: Props) {
  const { locale, id } = await params;
  if (!hasLocale(routing.locales, locale) || !LINK_ID.test(id)) notFound();
  setRequestLocale(locale);
  const { requestPayment, errors, uploads } = await getMessages();
  const t = await getTranslations({ locale, namespace: 'requestPayment' });
  return (
    <AppShell title={t('title')} back={`/bookings/${id}`}>
      <main className={styles.main} data-testid="request-payment-page">
        <NextIntlClientProvider messages={{ requestPayment, errors, uploads }}>
          <BookingRequestPayment bookingId={id} locale={locale} />
        </NextIntlClientProvider>
      </main>
    </AppShell>
  );
}

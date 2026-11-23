import { LINK_ID } from '@taskbeavo/app-links';
import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { hasLocale, NextIntlClientProvider } from 'next-intl';
import { getMessages, getTranslations, setRequestLocale } from 'next-intl/server';
import { BookingRelease } from '../../../../../components/release/booking-release';
import styles from '../../../../../components/release/release.module.css';
import { AppShell } from '../../../../../components/shell/app-shell';
import { routing } from '../../../../../i18n/routing';

type Props = PageProps<'/[locale]/bookings/[id]/release'>;

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) return {};
  const t = await getTranslations({ locale, namespace: 'release' });
  return { title: t('title'), robots: { index: false, follow: false } };
}

/**
 * Release payment (mockups 7d–7f), from the booking, the payment-requested email and push.
 * Opening it releases nothing: the poster confirms with a tap and the server checks the
 * booking. The helper answers a proposal to release less here too.
 */
export default async function BookingReleasePage({ params }: Props) {
  const { locale, id } = await params;
  if (!hasLocale(routing.locales, locale) || !LINK_ID.test(id)) notFound();
  setRequestLocale(locale);
  const { release, errors, checkout } = await getMessages();
  const t = await getTranslations({ locale, namespace: 'release' });
  return (
    <AppShell title={t('title')} back={`/bookings/${id}`}>
      <main className={styles.main} data-testid="release-page">
        <NextIntlClientProvider messages={{ release, errors, checkout }}>
          <BookingRelease bookingId={id} locale={locale} />
        </NextIntlClientProvider>
      </main>
    </AppShell>
  );
}

import { LINK_ID } from '@taskbeavo/app-links';
import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { hasLocale, NextIntlClientProvider } from 'next-intl';
import { getMessages, getTranslations, setRequestLocale } from 'next-intl/server';
import { BookingCancel } from '../../../../../components/cancel/booking-cancel';
import styles from '../../../../../components/cancel/cancel.module.css';
import { routing } from '../../../../../i18n/routing';
import { AppShell } from '../../../../../components/shell/app-shell';

type Props = PageProps<'/[locale]/bookings/[id]/cancel'>;

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) return {};
  const t = await getTranslations({ locale, namespace: 'cancel' });
  return { title: t('metaTitle'), robots: { index: false, follow: false } };
}

/** The poster's cancel and withdrawal button (mockup 13). */
export default async function BookingCancelPage({ params }: Props) {
  const { locale, id } = await params;
  if (!hasLocale(routing.locales, locale) || !LINK_ID.test(id)) notFound();
  setRequestLocale(locale);
  const { cancel, errors } = await getMessages();
  const t = await getTranslations({ locale, namespace: 'cancel' });
  return (
    <AppShell title={t('title')} back={`/bookings/${id}`}>
      <main className={styles.main}>
        <NextIntlClientProvider messages={{ cancel, errors }}>
          <BookingCancel bookingId={id} locale={locale} />
        </NextIntlClientProvider>
      </main>
    </AppShell>
  );
}

import { LINK_ID } from '@taskbeavo/app-links';
import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { hasLocale, NextIntlClientProvider } from 'next-intl';
import { getMessages, getTranslations, setRequestLocale } from 'next-intl/server';
import { BookingCheckout } from '../../../../../components/checkout/booking-checkout';
import styles from '../../../../../components/checkout/checkout.module.css';
import { routing } from '../../../../../i18n/routing';
import { AppShell } from '../../../../../components/shell/app-shell';

type Props = PageProps<'/[locale]/bookings/[id]/checkout'>;

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) return {};
  const t = await getTranslations({ locale, namespace: 'checkout' });
  return { title: t('metaTitle'), robots: { index: false, follow: false } };
}

/**
 * An open checkout or its outcome (mockups 6b–6f). Stripe's 3D Secure redirect returns here;
 * the payment's state always comes from the server.
 */
export default async function BookingCheckoutPage({ params }: Props) {
  const { locale, id } = await params;
  if (!hasLocale(routing.locales, locale) || !LINK_ID.test(id)) notFound();
  setRequestLocale(locale);
  const { checkout, errors } = await getMessages();
  const t = await getTranslations({ locale, namespace: 'checkout' });
  return (
    <AppShell title={t('title')} back={`/bookings/${id}`}>
      <main className={styles.main}>
        <NextIntlClientProvider messages={{ checkout, errors }}>
          <BookingCheckout bookingId={id} locale={locale} />
        </NextIntlClientProvider>
      </main>
    </AppShell>
  );
}

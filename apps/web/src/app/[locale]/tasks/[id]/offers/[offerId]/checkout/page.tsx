import { LINK_ID } from '@taskbeavo/app-links';
import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { hasLocale, NextIntlClientProvider } from 'next-intl';
import { getMessages, getTranslations, setRequestLocale } from 'next-intl/server';
import styles from '../../../../../../../components/checkout/checkout.module.css';
import { OfferCheckout } from '../../../../../../../components/checkout/offer-checkout';
import { routing } from '../../../../../../../i18n/routing';

type Props = PageProps<'/[locale]/tasks/[id]/offers/[offerId]/checkout'>;

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) return {};
  const t = await getTranslations({ locale, namespace: 'checkout' });
  // Private page: never in search results.
  return { title: t('metaTitle'), robots: { index: false, follow: false } };
}

/** Checkout of one offer (mockup 6a): summary, the two consent boxes, then the card form. */
export default async function OfferCheckoutPage({ params }: Props) {
  const { locale, id, offerId } = await params;
  if (!hasLocale(routing.locales, locale) || !LINK_ID.test(id) || !LINK_ID.test(offerId)) {
    notFound();
  }
  setRequestLocale(locale);
  const { checkout, errors, offers } = await getMessages();
  const t = await getTranslations({ locale, namespace: 'checkout' });
  return (
    <main className={styles.main}>
      <h1 className={styles.title}>{t('title')}</h1>
      <NextIntlClientProvider messages={{ checkout, errors, offers }}>
        <OfferCheckout taskId={id} offerId={offerId} locale={locale} />
      </NextIntlClientProvider>
    </main>
  );
}

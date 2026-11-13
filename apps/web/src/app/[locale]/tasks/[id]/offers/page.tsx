import { LINK_ID } from '@taskbeavo/app-links';
import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { hasLocale, NextIntlClientProvider } from 'next-intl';
import { getMessages, getTranslations, setRequestLocale } from 'next-intl/server';
import { OffersView } from '../../../../../components/offers/offers-view';
import { routing } from '../../../../../i18n/routing';

export async function generateMetadata({
  params,
}: PageProps<'/[locale]/tasks/[id]/offers'>): Promise<Metadata> {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) return {};
  const t = await getTranslations({ locale, namespace: 'offers' });
  // Private page: never in search results.
  return { title: t('metaTitle'), robots: { index: false, follow: false } };
}

/** The poster's offers on their task (mockups 4a, 4c). Signed-in, live, client-rendered. */
export default async function TaskOffersPage({ params }: PageProps<'/[locale]/tasks/[id]/offers'>) {
  const { locale, id } = await params;
  if (!hasLocale(routing.locales, locale) || !LINK_ID.test(id)) notFound();
  setRequestLocale(locale);
  const { offers } = await getMessages();
  return (
    <main>
      <NextIntlClientProvider messages={{ offers }}>
        <OffersView taskId={id} locale={locale} />
      </NextIntlClientProvider>
    </main>
  );
}

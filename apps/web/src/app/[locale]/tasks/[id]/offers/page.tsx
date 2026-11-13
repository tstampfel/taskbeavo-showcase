import { LINK_ID } from '@taskbeavo/app-links';
import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { hasLocale, NextIntlClientProvider } from 'next-intl';
import { getMessages, getTranslations, setRequestLocale } from 'next-intl/server';
import { OffersView } from '../../../../../components/offers/offers-view';
import { routing } from '../../../../../i18n/routing';
import { pickMessages, SHELL_NAMESPACES } from '../../../../../i18n/client-messages';

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
  // OffersView draws the top bar itself (its title counts the offers), so the shell's
  // namespaces come along.
  const messages = pickMessages(await getMessages(), [...SHELL_NAMESPACES, 'offers']);
  return (
    <NextIntlClientProvider messages={messages}>
      <OffersView taskId={id} locale={locale} />
    </NextIntlClientProvider>
  );
}

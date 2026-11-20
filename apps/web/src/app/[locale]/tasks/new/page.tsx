import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { hasLocale, NextIntlClientProvider } from 'next-intl';
import { getMessages, getTranslations, setRequestLocale } from 'next-intl/server';
import { PostTaskWizard } from '../../../../components/post-task/post-task-wizard';
import { AppShell } from '../../../../components/shell/app-shell';
import { pickMessages, SHELL_NAMESPACES } from '../../../../i18n/client-messages';
import { routing } from '../../../../i18n/routing';

export async function generateMetadata({
  params,
}: PageProps<'/[locale]/tasks/new'>): Promise<Metadata> {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) return {};
  const t = await getTranslations({ locale, namespace: 'postTask' });
  // Private page: never in search results.
  return { title: t('title'), robots: { index: false, follow: false } };
}

/** Post a task (mockup 03, BEAV-87). Signed-in, client-rendered; the draft stays in the browser. */
export default async function PostTaskPage({ params }: PageProps<'/[locale]/tasks/new'>) {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) notFound();
  setRequestLocale(locale);
  const t = await getTranslations({ locale, namespace: 'postTask' });
  const messages = pickMessages(await getMessages(), [
    ...SHELL_NAMESPACES,
    'postTask',
    'enums',
    'errors',
    'taskListing',
    'uploads',
  ]);
  // The top bar's back closes the wizard; the draft stays saved.
  return (
    <NextIntlClientProvider messages={messages}>
      <AppShell title={t('title')} back="/">
        <main>
          <PostTaskWizard locale={locale} />
        </main>
      </AppShell>
    </NextIntlClientProvider>
  );
}

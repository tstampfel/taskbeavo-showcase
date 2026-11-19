import { isLocale, type Locale } from '@taskbeavo/i18n';
import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { getTranslations, setRequestLocale } from 'next-intl/server';
import { cache } from 'react';
import { SiteHeader } from '../../../../components/shell/site-header';
import { loadListing } from '../../../../components/tasks/load-tasks';
import { PosterOffersLink } from '../../../../components/tasks/poster-offers-link';
import { TaskPageView } from '../../../../components/tasks/task-views';
import { localeAlternates, siteUrl } from '../../../../i18n/metadata';

function checked(locale: string): Locale {
  if (!isLocale(locale)) notFound();
  return locale;
}

/** One API call per request for the metadata and the page. */
const listing = cache(loadListing);

const DESCRIPTION_LENGTH = 155;

export async function generateMetadata({
  params,
}: PageProps<'/[locale]/tasks/[id]'>): Promise<Metadata> {
  const { locale: raw, id } = await params;
  const locale = checked(raw);
  const task = await listing(id);
  if (!task) return { robots: { index: false } };
  const t = await getTranslations({ locale });
  const text = task.description.replace(/\s+/g, ' ').trim();
  const description =
    text.length > DESCRIPTION_LENGTH ? `${text.slice(0, DESCRIPTION_LENGTH - 1)}…` : text;
  return {
    title: task.title,
    description: description || t('browse.metaDescription'),
    alternates: localeAlternates(locale, `/tasks/${encodeURIComponent(id)}`),
    openGraph: { title: task.title, description: description || undefined, type: 'website' },
  };
}

/**
 * /{locale}/tasks/{id}: a task's public page (BEAV-91). A server component that fetches the
 * listing without the visitor's session: the approximate area only (hard rule e). The poster
 * manages offers at /tasks/{id}/offers; the booked helper sees the address on the booking.
 */
export default async function TaskPage({ params }: PageProps<'/[locale]/tasks/[id]'>) {
  const { locale: raw, id } = await params;
  const locale = checked(raw);
  setRequestLocale(locale);
  const task = await listing(id);
  if (!task) notFound();
  const t = await getTranslations({ locale });
  const base = siteUrl();
  const breadcrumbs = {
    '@context': 'https://schema.org',
    '@type': 'BreadcrumbList',
    itemListElement: [
      {
        '@type': 'ListItem',
        position: 1,
        name: t('common.appName'),
        item: new URL(`/${locale}`, base).toString(),
      },
      {
        '@type': 'ListItem',
        position: 2,
        name: t('browse.title'),
        item: new URL(`/${locale}/tasks`, base).toString(),
      },
      {
        '@type': 'ListItem',
        position: 3,
        name: task.title,
        item: new URL(`/${locale}/tasks/${encodeURIComponent(id)}`, base).toString(),
      },
    ],
  };
  return (
    <>
      <SiteHeader />
      <script
        type="application/ld+json"
        // Built from catalogue strings, the task title and URLs; `<` escaped so the poster's
        // title can't close the tag.
        dangerouslySetInnerHTML={{
          __html: JSON.stringify(breadcrumbs).replace(/</g, '\\u003c'),
        }}
      />
      <TaskPageView
        task={task}
        locale={locale}
        actions={<PosterOffersLink taskId={id} label={t('taskListing.seeOffers')} />}
      />
    </>
  );
}

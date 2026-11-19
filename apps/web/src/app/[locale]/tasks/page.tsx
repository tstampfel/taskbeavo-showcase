import { isLocale, type Locale } from '@taskbeavo/i18n';
import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { getTranslations, setRequestLocale } from 'next-intl/server';
import { SiteHeader } from '../../../components/shell/site-header';
import { isCategory } from '../../../components/tasks/listing-text';
import { loadOpenTasks, type OpenTasksFilter } from '../../../components/tasks/load-tasks';
import { BrowseView } from '../../../components/tasks/task-views';
import { localeAlternates } from '../../../i18n/metadata';

function checked(locale: string): Locale {
  if (!isLocale(locale)) notFound();
  return locale;
}

function single(value: string | string[] | undefined): string | undefined {
  return typeof value === 'string' && value ? value : undefined;
}

/** The filter from `?area=…&category=…&after=…`; anything unknown is left out. */
function filterOf(search: Record<string, string | string[] | undefined>): OpenTasksFilter {
  const category = single(search.category);
  return {
    areaSlug: single(search.area),
    category: isCategory(category) ? category : undefined,
    after: single(search.after),
  };
}

export async function generateMetadata({
  params,
  searchParams,
}: PageProps<'/[locale]/tasks'>): Promise<Metadata> {
  const locale = checked((await params).locale);
  const filter = filterOf(await searchParams);
  const t = await getTranslations({ locale, namespace: 'browse' });
  return {
    title: t('title'),
    description: t('metaDescription'),
    alternates: localeAlternates(locale, '/tasks'),
    // Later pages and filtered lists point search engines at the first page (canonical).
    robots: filter.after || filter.category || filter.areaSlug ? { index: false } : undefined,
  };
}

/** /{locale}/tasks: the public browse list (BEAV-91, mockup 10). A server component. */
export default async function BrowsePage({ params, searchParams }: PageProps<'/[locale]/tasks'>) {
  const locale = checked((await params).locale);
  setRequestLocale(locale);
  const filter = filterOf(await searchParams);
  const page = await loadOpenTasks(filter);
  return (
    <>
      <SiteHeader />
      <BrowseView locale={locale} filter={filter} page={page} />
    </>
  );
}

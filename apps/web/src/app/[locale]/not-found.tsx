import { getLocale } from 'next-intl/server';
import { NotFoundView } from '../../components/not-found-view';
import { SiteHeader } from '../../components/shell/site-header';
import { routing } from '../../i18n/routing';
import { hasLocale } from 'next-intl';

export default async function LocaleNotFound() {
  const locale = await getLocale();
  return (
    <>
      <SiteHeader />
      <NotFoundView locale={hasLocale(routing.locales, locale) ? locale : routing.defaultLocale} />
    </>
  );
}

import { getLocale } from 'next-intl/server';
import { NotFoundView } from '../../components/not-found-view';
import { routing } from '../../i18n/routing';
import { hasLocale } from 'next-intl';

export default async function LocaleNotFound() {
  const locale = await getLocale();
  return (
    <NotFoundView locale={hasLocale(routing.locales, locale) ? locale : routing.defaultLocale} />
  );
}

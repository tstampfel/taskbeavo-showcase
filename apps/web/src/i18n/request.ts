import { catalogues, TASK_TIME_ZONE } from '@taskbeavo/i18n';
import { hasLocale } from 'next-intl';
import { getRequestConfig } from 'next-intl/server';
import { routing } from './routing';

export default getRequestConfig(async ({ requestLocale }) => {
  const requested = await requestLocale;
  const locale = hasLocale(routing.locales, requested) ? requested : routing.defaultLocale;
  // Tasks happen in Croatia, so times render in Zagreb time on the server and in every browser.
  return { locale, messages: catalogues[locale], timeZone: TASK_TIME_ZONE };
});

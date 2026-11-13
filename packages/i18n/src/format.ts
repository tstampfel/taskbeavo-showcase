import type { Locale } from './index';

/** Tasks happen in Croatia, so their times always show in Zagreb time. */
export const TASK_TIME_ZONE = 'Europe/Zagreb';

export type DateStyle = 'short' | 'medium' | 'long' | 'full';

/** Euro cents as each language writes money: "33,60 €" in hr, de and fr, "€33.60" in en. */
export function formatMoney(cents: number, locale: Locale, currency = 'EUR'): string {
  return new Intl.NumberFormat(locale, {
    style: 'currency',
    currency,
    // "€" rather than "EUR", which Croatian would otherwise get.
    currencyDisplay: 'narrowSymbol',
  }).format(cents / 100);
}

/** A date and time in Zagreb time. */
export function formatDateTime(
  value: Date | string,
  locale: Locale,
  style: DateStyle = 'medium',
): string {
  return new Intl.DateTimeFormat(locale, {
    dateStyle: style,
    timeStyle: 'short',
    timeZone: TASK_TIME_ZONE,
  }).format(new Date(value));
}

/** The calendar day in Zagreb. */
export function formatDate(
  value: Date | string,
  locale: Locale,
  style: DateStyle = 'medium',
): string {
  return new Intl.DateTimeFormat(locale, { dateStyle: style, timeZone: TASK_TIME_ZONE }).format(
    new Date(value),
  );
}

/** A whole percent as each language writes it: "12%" in en, "12 %" in hr, de and fr. */
export function formatPercent(percent: number, locale: Locale): string {
  return new Intl.NumberFormat(locale, { style: 'percent', maximumFractionDigits: 2 }).format(
    percent / 100,
  );
}

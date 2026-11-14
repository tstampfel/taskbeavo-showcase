import type { Locale } from '@taskbeavo/i18n';

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/**
 * How long ago something happened, in the page language ("2 days ago", "vor 2 Tagen",
 * "il y a 2 jours", "prije 2 dana"): minutes under an hour, hours under a day, then days.
 * A time a little in the future (clock skew) reads as now.
 */
export function timeAgo(value: unknown, now: Date, locale: Locale): string {
  const elapsed = Math.max(0, now.getTime() - new Date(String(value)).getTime());
  const format = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' });
  if (elapsed < MINUTE) return format.format(0, 'second');
  if (elapsed < HOUR) return format.format(-Math.floor(elapsed / MINUTE), 'minute');
  if (elapsed < DAY) return format.format(-Math.floor(elapsed / HOUR), 'hour');
  return format.format(-Math.floor(elapsed / DAY), 'day');
}

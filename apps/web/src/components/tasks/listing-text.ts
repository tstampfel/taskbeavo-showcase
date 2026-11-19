import type { TaskCategory, TaskListingFieldsFragment } from '@taskbeavo/graphql';
import { formatDate, formatMoney, formatTime, type Locale } from '@taskbeavo/i18n';

export type Listing = TaskListingFieldsFragment;

export const CATEGORIES: readonly TaskCategory[] = [
  'GARDEN',
  'ERRANDS',
  'REPAIRS',
  'CLEANING',
  'CHECK_IN',
];

export function isCategory(value: unknown): value is TaskCategory {
  return typeof value === 'string' && (CATEGORIES as readonly string[]).includes(value);
}

/** next-intl's `t` for the whole catalogue, as far as these lines need it. */
type T = (key: string, values?: Record<string, string | number>) => string;

/**
 * When, in Zagreb time: "22 Nov 2026, 09:00–11:00" for a window within one day, both dates
 * when it spans several ("22 Nov 2026, 09:00 – 25 Nov 2026, 17:00"), or "Any day suits".
 */
export function listingWhen(listing: Listing, t: T, locale: Locale): string {
  if (listing.flexibleDate || !listing.windowStart || !listing.windowEnd) {
    return t('taskListing.flexibleDate');
  }
  const start = String(listing.windowStart);
  const end = String(listing.windowEnd);
  const startDate = formatDate(start, locale);
  const endDate = formatDate(end, locale);
  if (startDate !== endDate) {
    return t('taskListing.windowDays', {
      startDate,
      start: formatTime(start, locale),
      endDate,
      end: formatTime(end, locale),
    });
  }
  return t('taskListing.window', {
    date: startDate,
    start: formatTime(start, locale),
    end: formatTime(end, locale),
  });
}

export function listingBudget(listing: Listing, t: T, locale: Locale): string {
  return listing.budgetCents === null
    ? t('taskListing.noBudget')
    : t('taskListing.budget', { amount: formatMoney(listing.budgetCents, locale) });
}

/** The approximate area only (hard rule e). */
export function listingArea(listing: Listing, t: T): string | null {
  return listing.serviceAreaName ? t('taskListing.area', { area: listing.serviceAreaName }) : null;
}

export function listingCategory(listing: Listing, t: T): string {
  return listing.category ? t(`enums.TaskCategory.${listing.category}`) : t('taskListing.title');
}

import type { TaskListingFieldsFragment } from '@taskbeavo/graphql';
import { formatDate, formatMoney, formatTime, type Locale } from '@taskbeavo/i18n';
import type { TFunction } from 'i18next';

type Listing = Pick<
  TaskListingFieldsFragment,
  'flexibleDate' | 'windowStart' | 'windowEnd' | 'budgetCents' | 'serviceAreaName' | 'category'
>;

/** When: "Sat 22 Nov 2026, 09:00–11:00" in Zagreb time, or "Any day suits". */
export function listingWhen(listing: Listing, t: TFunction, locale: Locale): string {
  if (listing.flexibleDate || !listing.windowStart || !listing.windowEnd) {
    return t('taskListing.flexibleDate');
  }
  const start = String(listing.windowStart);
  const end = String(listing.windowEnd);
  return t('taskListing.window', {
    date: formatDate(start, locale, 'medium'),
    start: formatTime(start, locale),
    end: formatTime(end, locale),
  });
}

export function listingBudget(listing: Listing, t: TFunction, locale: Locale): string {
  return listing.budgetCents === null
    ? t('taskListing.noBudget')
    : t('taskListing.budget', { amount: formatMoney(listing.budgetCents, locale) });
}

/** The approximate area only (hard rule e): the service area's name. */
export function listingArea(listing: Listing, t: TFunction): string | null {
  return listing.serviceAreaName ? t('taskListing.area', { area: listing.serviceAreaName }) : null;
}

export function listingCategory(listing: Listing, t: TFunction): string {
  return listing.category ? t(`enums.TaskCategory.${listing.category}`) : t('taskListing.title');
}

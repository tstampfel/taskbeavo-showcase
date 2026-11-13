import type { FeeSchedule } from './fees-view';

/** The last day an earlier list applied: the day before the next one's effective date. */
export function dayBefore(date: string): string {
  const day = new Date(`${date}T12:00:00Z`);
  day.setUTCDate(day.getUTCDate() - 1);
  return day.toISOString().slice(0, 10);
}

/** "2.50": euro cents as a plain decimal, the same in every locale. */
function euros(cents: number): string {
  return (cents / 100).toFixed(2);
}

/** The `vat` column: the fee includes VAT at a rate (variant A), carries none (B), or TBC. */
function vatColumn(schedule: FeeSchedule): string {
  if (schedule.vat === 'INCLUDED') return `included_${schedule.vatPercent}`;
  return schedule.vat === 'NOT_CHARGED' ? 'not_charged' : 'TBC';
}

/**
 * The fee as one price: "12% (min. 2.50 EUR)". The service fee has no fixed amount, so the
 * retail and anchor price columns state the rule, and `rate_percent` and `minimum_eur` give
 * its two numbers for a machine (DRAFT representation, for the lawyer: REVIEW.md).
 */
function feeRule(schedule: FeeSchedule): string {
  return `${schedule.percent}% (min. ${euros(schedule.minimumCents)} EUR)`;
}

/** The service as NN 101/2026 names it, in Croatian like the rest of the official file. */
export const PRICE_LIST_SERVICE_NAME = 'Naknada za uslugu TaskBeavo';

export const PRICE_LIST_COLUMNS = [
  'version',
  'valid_from',
  'valid_until',
  'service',
  'charged_to',
  'basis',
  'rate_percent',
  'minimum_eur',
  'currency',
  'vat',
  // NN 101/2026 (from 17 November 2026): the service name, the retail price, whether it is
  // a special offer, and the anchor price. TaskBeavo was not live on 10 September 2026, so
  // the anchor price is the price it was first offered at: the earliest price list.
  'service_name',
  'retail_price',
  'special_offer',
  'anchor_price',
  'anchor_price_from',
] as const;

/**
 * The machine-readable price list (NN 101/2026) for `version`, built from the same API data as
 * the /fees page. Dates are Europe/Zagreb days; `valid_until` is empty while a list applies.
 * `vat` is TBC until the accountant sets the schedule's VAT variant (backend/bookings/money.py).
 * The API publishes the schedule that turns current at 00:00 Zagreb on its effective date and
 * the route caches for 5 minutes, so the file is up to date long before 08:00 that day; every
 * published version stays downloadable as `pricelist-<version>.csv` for good.
 */
export function priceListCsv(schedules: FeeSchedule[], version: string): string | null {
  const index = schedules.findIndex((schedule) => schedule.version === version);
  const schedule = schedules[index];
  if (!schedule) return null;
  const next = schedules[index - 1];
  // Newest first, so the earliest list (the price at first offer) is the last.
  const first = schedules[schedules.length - 1] ?? schedule;
  const row = [
    schedule.version,
    String(schedule.effectiveFrom),
    next ? dayBefore(String(next.effectiveFrom)) : '',
    'service_fee',
    'poster',
    'task_price',
    String(schedule.percent),
    euros(schedule.minimumCents),
    'EUR',
    vatColumn(schedule),
    PRICE_LIST_SERVICE_NAME,
    feeRule(schedule),
    'no',
    feeRule(first),
    String(first.effectiveFrom),
  ];
  return `${PRICE_LIST_COLUMNS.join(',')}\r\n${row.join(',')}\r\n`;
}

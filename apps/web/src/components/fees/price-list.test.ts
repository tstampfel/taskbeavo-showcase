import { describe, expect, it } from 'vitest';
import type { FeeSchedule } from './fees-view';
import { PRICE_LIST_COLUMNS, priceListCsv } from './price-list';

function schedule(
  version: string,
  effectiveFrom: string,
  percent: number,
  minimumCents: number,
  status: FeeSchedule['status'],
): FeeSchedule {
  return {
    id: version,
    version,
    effectiveFrom,
    percent,
    minimumCents,
    status,
    vat: 'UNDECIDED',
    vatPercent: 25,
    examples: [],
  };
}

function row(csv: string | null): Record<string, string> {
  const [header, values] = (csv ?? '').trimEnd().split('\r\n');
  const keys = (header ?? '').split(',');
  const cells = (values ?? '').split(',');
  return Object.fromEntries(keys.map((key, i) => [key, cells[i] ?? '']));
}

describe('priceListCsv (NN 101/2026)', () => {
  it('states the fee rule as the retail price, with no special offer', () => {
    const csv = priceListCsv([schedule('1', '2027-01-01', 12, 250, 'CURRENT')], '1');

    expect(Object.keys(row(csv))).toEqual([...PRICE_LIST_COLUMNS]);
    expect(row(csv)).toMatchObject({
      service_name: 'Naknada za uslugu TaskBeavo',
      retail_price: '12% (min. 2.50 EUR)',
      special_offer: 'no',
      // Not live on 10 September 2026: the anchor is the price at first offer.
      anchor_price: '12% (min. 2.50 EUR)',
      anchor_price_from: '2027-01-01',
      rate_percent: '12',
      minimum_eur: '2.50',
    });
  });

  it('keeps the first price as the anchor after a change, and every version downloadable', () => {
    // On the change day the API already marks the new list CURRENT (from 00:00 Zagreb).
    const schedules = [
      schedule('2', '2027-06-01', 15, 300, 'CURRENT'),
      schedule('1', '2027-01-01', 12, 250, 'EARLIER'),
    ];

    expect(row(priceListCsv(schedules, '2'))).toMatchObject({
      retail_price: '15% (min. 3.00 EUR)',
      anchor_price: '12% (min. 2.50 EUR)',
      anchor_price_from: '2027-01-01',
      valid_until: '',
    });
    expect(row(priceListCsv(schedules, '1'))).toMatchObject({
      retail_price: '12% (min. 2.50 EUR)',
      valid_until: '2027-05-31',
    });
    expect(priceListCsv(schedules, '3')).toBeNull();
  });
});

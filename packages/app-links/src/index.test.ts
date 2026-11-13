import { locales } from '@taskbeavo/i18n';
import { describe, expect, it } from 'vitest';
import {
  feesPath,
  rankingPath,
  rankingUrl,
  feesUrl,
  isAppRoute,
  legalPath,
  legalUrl,
  LEGAL_PAGES,
  LINK_ID,
  LINK_LOCALES,
  linkedPathPrefixes,
  priceListCsvPath,
} from './index';

describe('app links', () => {
  it('use the same locale prefixes as the web app', () => {
    expect([...LINK_LOCALES]).toEqual([...locales]);
  });

  it('cover each section with and without a locale prefix', () => {
    const prefixes = linkedPathPrefixes();
    expect(prefixes).toEqual(
      expect.arrayContaining(['/tasks/', '/bookings/', '/de/bookings/', '/hr/conversations/']),
    );
    expect(prefixes).toHaveLength(20);
  });

  it.each([
    '/tasks/42',
    '/tasks/42/offers',
    '/tasks/42/offers/7/checkout',
    '/bookings/7',
    '/bookings/7/checkout',
    '/bookings/7/release',
    '/bookings/7/no-access',
    '/conversations/abc_DEF-9',
    '/helper/payouts',
    '/helper/payouts/return',
    '/helper/payouts/refresh',
  ])('open the screen at %s', (path) => {
    expect(isAppRoute(path)).toBe(true);
  });

  it.each([
    '/',
    '/tasks',
    '/tasks/',
    '/bookings/7/pay',
    '/tasks/42/offers/7',
    '/tasks/42/offers/../checkout',
    '/bookings/7/release/x',
    '/de/tasks/1',
    '/helper',
    '/helper/payouts/7',
  ])('refuse %s', (path) => {
    expect(isAppRoute(path)).toBe(false);
  });

  it('accept only short opaque IDs', () => {
    expect(LINK_ID.test('42')).toBe(true);
    expect(LINK_ID.test('a'.repeat(65))).toBe(false);
    expect(LINK_ID.test('..%2F')).toBe(false);
    expect(LINK_ID.test('a/b')).toBe(false);
  });
});

describe('legal pages', () => {
  it('live under a locale, optionally at one version', () => {
    expect(legalPath('helper-terms', 'de')).toBe('/de/helper-terms');
    expect(legalPath('terms', 'fr', '2026-11')).toBe('/fr/terms/2026-11');
    expect(legalUrl('cancellation', 'hr')).toBe('https://taskbeavo.com/hr/cancellation');
  });

  it('never open the app', () => {
    for (const page of LEGAL_PAGES) {
      expect(isAppRoute(`/${page}`)).toBe(false);
      expect(linkedPathPrefixes().some((prefix) => legalPath(page, 'en').startsWith(prefix))).toBe(
        false,
      );
    }
  });
});

describe('fee page', () => {
  it('lives under a locale, with its archive and the CSV beside it', () => {
    expect(feesPath('hr')).toBe('/hr/fees');
    expect(feesPath('de', true)).toBe('/de/fees/archive');
    expect(rankingPath('fr')).toBe('/fr/ranking');
    expect(rankingUrl('hr')).toMatch(/^https:\/\/[^/]+\/hr\/ranking$/);
    expect(feesUrl('fr')).toBe('https://taskbeavo.com/fr/fees');
    expect(priceListCsvPath()).toBe('/fees/pricelist.csv');
    expect(priceListCsvPath('2026-11')).toBe('/fees/pricelist-2026-11.csv');
  });

  it('never opens the app', () => {
    for (const path of [feesPath('en'), feesPath('en', true), priceListCsvPath()]) {
      expect(linkedPathPrefixes().some((prefix) => path.startsWith(prefix))).toBe(false);
    }
  });
});

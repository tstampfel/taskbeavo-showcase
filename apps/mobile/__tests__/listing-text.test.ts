import { catalogues, formatDate, formatTime, locales } from '@taskbeavo/i18n';
import { i18n } from '../src/i18n';
import { listingWhen } from '../src/tasks/listing-text';

const base = { budgetCents: null, serviceAreaName: null, category: null, flexibleDate: false };

describe('listingWhen', () => {
  afterAll(async () => {
    await i18n.changeLanguage('hr');
  });

  it.each(locales)('names both days of a window over several days in %s', async (locale) => {
    await i18n.changeLanguage(locale);
    const start = '2026-11-22T08:00:00Z';
    const end = '2026-11-25T16:00:00Z';

    const when = listingWhen({ ...base, windowStart: start, windowEnd: end }, i18n.t, locale);

    expect(when).toBe(
      catalogues[locale].taskListing.windowDays
        .replace('{startDate}', formatDate(start, locale, 'medium'))
        .replace('{start}', formatTime(start, locale))
        .replace('{endDate}', formatDate(end, locale, 'medium'))
        .replace('{end}', formatTime(end, locale)),
    );
  });

  it('gives one date for a window within a Zagreb day', async () => {
    await i18n.changeLanguage('en');
    // 23:30 UTC is 00:30 the next day in Zagreb: both ends fall on 23 Nov there.
    const start = '2026-11-22T23:30:00Z';
    const end = '2026-11-23T10:00:00Z';

    const when = listingWhen({ ...base, windowStart: start, windowEnd: end }, i18n.t, 'en');

    expect(when).toBe(
      `${formatDate(start, 'en', 'medium')}, ${formatTime(start, 'en')}–${formatTime(end, 'en')}`,
    );
    expect(when).not.toContain(' – ');
  });
});

import { TASK_TIME_ZONE, catalogues, locales, type Locale } from '@taskbeavo/i18n';
import { NextIntlClientProvider } from 'next-intl';
import type { ReactNode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { FeesArchiveView, FeesView, type CancellationAmounts, type FeeSchedule } from './fees-view';
import { priceListCsv } from './price-list';

// Intl separates numbers from units with no-break spaces; compare with plain spaces.
const plain = (text: string) => text.replace(/[\u00a0\u202f]/g, ' ');

function html(locale: Locale, node: ReactNode) {
  return plain(
    renderToStaticMarkup(
      <NextIntlClientProvider
        locale={locale}
        messages={catalogues[locale]}
        timeZone={TASK_TIME_ZONE}
      >
        {node}
      </NextIntlClientProvider>,
    ),
  );
}

/** What the API returns today (backend/bookings/money.py). */
const current: FeeSchedule = {
  id: '2026-11',
  version: '2026-11',
  effectiveFrom: '2026-11-01',
  percent: 12,
  minimumCents: 250,
  status: 'CURRENT',
  vat: 'UNDECIDED',
  vatPercent: 25,
  examples: [
    { id: '2026-11:4000', taskPriceCents: 4000, feeCents: 480, totalCents: 4480, vatCents: null },
    { id: '2026-11:1500', taskPriceCents: 1500, feeCents: 250, totalCents: 1750, vatCents: null },
  ],
};

/** A hypothetical fee change: the page must follow whatever the API serves. */
const later: FeeSchedule = {
  ...current,
  id: '2027-06',
  version: '2027-06',
  effectiveFrom: '2027-06-01',
  percent: 15,
  minimumCents: 300,
  examples: [
    { id: '2027-06:4000', taskPriceCents: 4000, feeCents: 600, totalCents: 4600, vatCents: null },
  ],
};

/** bookings/cancellation.py today (hard rule g). */
const cancellation: CancellationAmounts = {
  id: 'current',
  gracePeriodMinutes: 60,
  lateWindowHours: 24,
  latePayPercent: 25,
  latePayMinCents: 500,
  latePayMaxCents: 2000,
  callOutPercent: 25,
  callOutMinCents: 500,
  callOutMaxCents: 1500,
  keptFeePercent: 12,
  helperCancelCreditCents: 500,
  withdrawalPeriodDays: 14,
};

const fees = (
  locale: Locale,
  schedule = current,
  extra: Partial<Parameters<typeof FeesView>[0]> = {},
) =>
  html(
    locale,
    <FeesView schedule={schedule} cancellation={cancellation} locale={locale} {...extra} />,
  );

const vatParagraph = (markup: string) =>
  markup.match(/<p data-testid="fees-vat">(.*?)<\/p>/)?.[1] ?? '';

const row = (markup: string, key: string) =>
  markup.match(new RegExp(`<tr data-testid="fees-refund-${key}">(.*?)</tr>`))?.[1] ?? '';

describe('FeesView', () => {
  it("renders the lawyer's draft with the API's numbers", () => {
    const markup = fees('en');

    expect(markup).toContain(
      '>Fees — price list valid from November 1, 2026 (version 2026-11)</h1>',
    );
    expect(markup).toContain(
      'TaskBeavo charges a service fee of 12% of the task price, minimum €2.50, per booking. The poster pays it. Helpers pay no fee to TaskBeavo [confirm].',
    );
    expect(markup).toContain('Task price €40.00 → service fee €4.80 → total €44.80.');
    expect(markup).toContain('Task price €15.00 → fee €2.50 (minimum) → total €17.50.');
    expect(markup).toContain('The task price is set by the helper.');
    expect(markup).toContain('We issue a fiscalised receipt for every service fee.');
    expect(markup).toContain('data-testid="fees-draft"');
    expect(markup).toContain('href="/en/cancellation"');
    expect(markup).toContain('href="/en/terms"');
    expect(markup).toContain('href="/en/fees/archive"');
    expect(markup).toContain('href="/fees/pricelist.csv"');
    expect(markup).toContain('href="/en/ranking"');
    expect(markup).not.toContain('data-testid="fees-upcoming"');
  });

  it('states neither VAT variant until the accountant picks one', () => {
    const undecided = fees('en');
    expect(undecided).toContain('[Open: the accountant decides');
    expect(undecided).not.toContain('includes 25% Croatian VAT');
    expect(undecided).not.toContain('not in the VAT system, so');

    const included = fees('en', {
      ...current,
      vat: 'INCLUDED',
      examples: current.examples.map((example) => ({
        ...example,
        vatCents: example.feeCents === 480 ? 96 : 50,
      })),
    });
    expect(included).toContain(
      'The service fee includes 25% Croatian VAT (in the example, €0.96 of €4.80). [Accountant to confirm.]',
    );

    expect(fees('hr', { ...current, vat: 'NOT_CHARGED' })).toContain(
      'TaskBeavo nije u sustavu PDV-a, pa se na naknadu ne obračunava PDV',
    );
  });

  // BEAV-194: the launch position, outside the VAT system, with the statutory citation in every
  // locale (the same sentence as backend/vat/wording.py; en, de and fr are drafts, REVIEW.md).
  it.each([
    ['hr', 'PDV nije obračunat temeljem čl. 90. st. 1. Zakona o PDV-u.'],
    ['en', 'VAT not charged under Article 90(1) of the Croatian VAT Act (Zakon o PDV-u).'],
    [
      'de',
      'Keine Mehrwertsteuer berechnet gemäß Art. 90 Abs. 1 des kroatischen Mehrwertsteuergesetzes (Zakon o PDV-u).',
    ],
    [
      'fr',
      'TVA non appliquée en vertu de l’article 90, paragraphe 1, de la loi croate relative à la TVA (Zakon o PDV-u).',
    ],
  ] as const)('states the čl. 90 st. 1 exemption in %s', (locale, note) => {
    const vat = vatParagraph(fees(locale, { ...current, vat: 'NOT_CHARGED' }));
    expect(vat).toContain(note);
    expect(vat).not.toContain('[');
  });

  it.each(locales)('shows the %s VAT note in the page language', (locale) => {
    expect(vatParagraph(fees(locale, { ...current, vat: 'NOT_CHARGED' }))).not.toContain('lang=');
  });

  it('builds the refund table from the cancellation rules', () => {
    const markup = fees('en');

    expect(row(markup, 'early')).toContain(
      'Cancelled within 1 hour of accepting, or more than 24 h before start',
    );
    expect(row(markup, 'late')).toContain('after the 14-day period');
    expect(row(markup, 'late')).toContain(
      '25% (min €5.00, max €20.00) goes to the helper; the rest is refunded',
    );
    expect(row(markup, 'late')).toContain('12% of the amount paid to the helper is kept');
    expect(row(markup, 'withdraw-after')).toContain('You pay the part done, pro rata');
    expect(row(markup, 'call-out')).toContain('25% (min €5.00, max €15.00) goes to the helper');
    expect(row(markup, 'helper-cancel')).toContain(
      'Refunded, plus €5.00 credit if under 24 h before start',
    );
    expect(row(markup, 'no-fault')).toContain('<td>Refunded</td><td>Refunded</td>');
    expect(row(markup, 'before-accept')).toContain('Free: nothing has been paid yet');
    expect(row(markup, 'work-started')).toContain('Not possible: open a dispute instead');
    expect(row(markup, 'payment-requested')).toContain('Not a cancellation');
    expect(markup).not.toContain('48');
    expect(markup).toContain('the one in force when you accepted the offer');

    const changed = html(
      'en',
      <FeesView
        schedule={current}
        cancellation={{ ...cancellation, latePayMaxCents: 2500, lateWindowHours: 48 }}
        locale="en"
      />,
    );
    expect(row(changed, 'late')).toContain('less than 48 h');
    expect(row(changed, 'late')).toContain('max €25.00');
  });

  it('takes every number from the API, never from the page', () => {
    const markup = fees('hr', later);

    expect(markup).toContain('naknadu za uslugu od 15 % cijene zadatka, a najmanje 3,00 €');
    expect(markup).toContain('46,00 €');
    expect(markup).not.toContain('2,50');
  });

  it('lists an announced change with its date, while charging the current list', () => {
    const markup = fees('en', current, { upcoming: [{ ...later, status: 'UPCOMING' }] });

    expect(markup).toContain('minimum €2.50, per booking');
    expect(markup).toMatch(
      /data-testid="fees-upcoming".*From June 1, 2027:<\/strong> 15% of the task price, at least €3.00\./,
    );
    expect(markup).toContain('href="/fees/pricelist-2027-06.csv"');
  });

  it.each(locales)('renders in %s', (locale) => {
    const markup = fees(locale);
    expect(markup).toContain('data-testid="fees-refunds"');
    expect(markup).toContain(`href="/${locale}/cancellation"`);
  });

  it.each(locales)('shows the %s page in its own language, with no English left', (locale) => {
    const markup = fees(locale);
    expect(markup).not.toContain('lang="en"');
    if (locale !== 'en') expect(markup).not.toContain('Task price');
  });
});

describe('the fee page copy', () => {
  it.each(locales)('in %s carries no fee or refund amounts of its own', (locale) => {
    // The statutory citation of the VAT exemption (ZPDV čl. 90 st. 1, BEAV-194) is a law's
    // article number, not an amount.
    const citation =
      /čl\. 90\. st\. 1\.|Article 90\(1\)|Art\. 90 Abs\. 1|article 90, paragraphe 1/g;
    const copy = JSON.stringify(catalogues[locale].fees).replace(citation, '');
    // Rates, minimums, caps and credits come from the API; a copied one would drift.
    expect(copy).not.toMatch(/\d+\s?%|\d+[.,]\d\d|€|\b(1|14|24)\b/);
  });
});

describe('FeesArchiveView', () => {
  it('lists every price list with the days it applied and its CSV', () => {
    const earlier = { ...current, status: 'EARLIER' as const };
    const markup = html('en', <FeesArchiveView schedules={[later, earlier]} locale="en" />);

    expect(markup).toContain('From June 1, 2027 · in effect now');
    expect(markup).toContain('From November 1, 2026 to May 31, 2027');
    expect(markup).toContain('href="/fees/pricelist-2027-06.csv"');
    expect(markup).toContain('href="/fees/pricelist-2026-11.csv"');
  });
});

describe('priceListCsv', () => {
  it('is the same schedule as the page, one row per list', () => {
    expect(priceListCsv([current], '2026-11')).toBe(
      'version,valid_from,valid_until,service,charged_to,basis,rate_percent,minimum_eur,currency,vat,' +
        'service_name,retail_price,special_offer,anchor_price,anchor_price_from\r\n' +
        '2026-11,2026-11-01,,service_fee,poster,task_price,12,2.50,EUR,TBC,' +
        'Naknada za uslugu TaskBeavo,12% (min. 2.50 EUR),no,12% (min. 2.50 EUR),2026-11-01\r\n',
    );
  });

  it('closes an earlier list the day before the next one applies', () => {
    const earlier = { ...current, status: 'EARLIER' as const };
    expect(priceListCsv([later, earlier], '2026-11')).toContain(
      '2026-11,2026-11-01,2027-05-31,service_fee',
    );
    expect(priceListCsv([later, earlier], '2027-06')).toContain('2027-06,2027-06-01,,');
  });

  it('states the VAT variant once the accountant picks one', () => {
    expect(priceListCsv([{ ...current, vat: 'INCLUDED' }], '2026-11')).toContain(',included_25,');
    expect(priceListCsv([{ ...current, vat: 'NOT_CHARGED' }], '2026-11')).toContain(
      ',not_charged,',
    );
  });

  it('knows only published versions', () => {
    expect(priceListCsv([current], '2099-01')).toBeNull();
  });
});

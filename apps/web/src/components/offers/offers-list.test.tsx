import { TASK_TIME_ZONE, catalogues, formatMoney, locales, type Locale } from '@taskbeavo/i18n';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { createTranslator, NextIntlClientProvider } from 'next-intl';
import { OffersList, offersTitle, type OffersListProps } from './offers-list';

const NOW = new Date('2026-11-05T10:00:00Z');

// 12%, at least €2.50, as the API computes it from the price list in force.
function fee(priceCents: number) {
  return Math.max(250, Math.round(priceCents * 0.12));
}

function offer(id: string, extra: Record<string, unknown> = {}) {
  const priceCents = typeof extra.priceCents === 'number' ? extra.priceCents : 3_000;
  return {
    __typename: 'Offer' as const,
    id,
    taskId: 'task-1',
    helperUserId: `helper-${id}`,
    helperIsTrader: false,
    helperBusinessName: null,
    helperBusinessOib: null,
    helperBusinessAddress: null,
    status: 'SENT' as const,
    priceCents: 3_000,
    serviceFeeCents: fee(priceCents),
    serviceFeeCreditCents: 0,
    serviceFeeScheduleVersion: '1',
    serviceFeePercent: 12,
    serviceFeeMinimumCents: 250,
    totalCents: priceCents + fee(priceCents),
    message: '',
    messageMasked: null,
    withdrawnReason: null,
    expiresAt: '2026-11-10T10:00:00Z',
    isMine: false,
    createdAt: '2026-11-03T10:00:00Z',
    updatedAt: '2026-11-03T10:00:00Z',
    ...extra,
  } as OffersListProps['offers'][number];
}

const OFFERS = [
  offer('withdrawn', { status: 'WITHDRAWN', priceCents: 2_400 }),
  offer('marko', { message: 'I have my own mower.' }),
  offer('stjepan', {
    priceCents: 2_800,
    helperIsTrader: true,
    helperBusinessName: 'Obrt Stjepan Popravci',
    helperBusinessOib: '00000000698',
    helperBusinessAddress: 'Obala Stjepana Radića 12, 20000 Dubrovnik, HR',
  }),
  offer('changed', { status: 'NEEDS_UPDATE', priceCents: 4_000 }),
  offer('late', { expiresAt: '2026-11-05T09:00:00Z', priceCents: 2_000 }),
];

function render(locale: Locale, props: Partial<OffersListProps> = {}) {
  return renderToStaticMarkup(
    <NextIntlClientProvider locale={locale} messages={catalogues[locale]} timeZone={TASK_TIME_ZONE}>
      <OffersList
        locale={locale}
        offers={OFFERS}
        task={{
          title: 'Mow lawn for Mama',
          windowStart: '2026-11-07T08:00:00Z',
          budgetCents: 3_500,
        }}
        sort="newest"
        comparing={false}
        now={NOW}
        onSort={vi.fn()}
        onCompare={vi.fn()}
        onAccept={vi.fn()}
        {...props}
      />
    </NextIntlClientProvider>,
  );
}

const cardOrder = (markup: string) =>
  [...markup.matchAll(/data-testid="offer-card-([^"]+)"/g)].map((m) => m[1]);

/** The markup of one card, up to the next card. */
const card = (markup: string, id: string) =>
  markup.split(`data-testid="offer-card-${id}"`)[1]!.split('data-testid="offer-card-')[0]!;

describe('OffersList', () => {
  it('lists open offers newest first, then withdrawn and expired ones greyed at the end', () => {
    const markup = render('en');

    // The count is the page title, in the top bar (offersTitle), not a second heading here.
    expect(markup).not.toContain('<h1');
    expect(cardOrder(markup)).toEqual(['marko', 'stjepan', 'changed', 'withdrawn', 'late']);
    expect(card(markup, 'withdrawn')).toContain('Offer withdrawn');
    expect(card(markup, 'late')).toContain('Offer expired');
    expect(card(markup, 'withdrawn')).not.toContain('Accept');
  });

  it('sorts open offers by lowest price', () => {
    expect(cardOrder(render('en', { sort: 'lowestPrice' }))).toEqual([
      'stjepan',
      'marko',
      'changed',
      'withdrawn',
      'late',
    ]);
  });

  it('shows the total, the helper price, trader label, new-helper state, expiry and note', () => {
    const markup = render('en');
    const marko = card(markup, 'marko');

    // The all-in total the poster pays first, the helper's price second (BEAV-191).
    expect(marko.indexOf(formatMoney(3_360, 'en'))).toBeLessThan(
      marko.indexOf(`Helper&#x27;s price ${formatMoney(3_000, 'en')}`),
    );
    expect(marko).toContain(`Helper&#x27;s price ${formatMoney(3_000, 'en')}`);
    expect(marko).toContain('in total, incl. service fee');
    expect(marko).toContain('Private individual');
    expect(marko).toContain('New helper · no reviews yet');
    expect(marko).toContain('Open until');
    expect(marko).toContain('“I have my own mower.”');
    expect(card(markup, 'stjepan')).toContain('Registered business');
    expect(card(markup, 'stjepan')).not.toContain('“');
  });

  it('names a trader helper’s business, OIB and registered address on its card', () => {
    const stjepan = card(render('en'), 'stjepan');

    expect(stjepan).toContain('Obrt Stjepan Popravci · OIB 00000000698');
    expect(stjepan).toContain(
      'Registered business address: Obala Stjepana Radića 12, 20000 Dubrovnik, HR',
    );
    expect(stjepan).toContain('Contact: in-app chat');
    expect(stjepan).not.toContain('data-testid="private-notice"');
  });

  it('says a private helper is not a trader, and never shows business details', () => {
    const marko = card(render('en'), 'marko');

    expect(marko).toContain('data-testid="private-notice"');
    expect(marko).toContain('A private individual, not a trader');
    expect(marko).not.toContain('data-testid="trader-identity"');
    expect(marko).not.toContain('OIB');
  });

  it('says on every open offer how obligations are split', () => {
    const markup = render('en');
    for (const id of ['marko', 'stjepan', 'changed']) {
      expect(card(markup, id)).toContain(
        'TaskBeavo handles payment and support; the helper is responsible for doing the task.',
      );
    }
  });

  it('only lets the poster accept an offer that is SENT and not expired', () => {
    const markup = render('en');
    const label = (cents: number) => `aria-label="Accept the offer of ${formatMoney(cents, 'en')}"`;

    expect(card(markup, 'marko')).toMatch(/<button type="button" class="[^"]*"[^>]*aria-label/);
    expect(card(markup, 'marko')).not.toMatch(/disabled=""[^>]*aria-label/);
    expect(card(markup, 'marko')).toContain(label(3_360));
    expect(card(markup, 'changed')).toContain('needs to confirm this offer');
    expect(card(markup, 'changed')).toMatch(/disabled=""[^>]*aria-label="Accept the offer/);
  });

  for (const locale of locales) {
    it(`titles the page with every offer, or the open ones compared, in ${locale}`, () => {
      const t = createTranslator({ locale, messages: catalogues[locale], namespace: 'offers' });
      const list = offersTitle(t, OFFERS, { sort: 'newest', comparing: false, now: NOW });
      const compared = offersTitle(t, OFFERS, { sort: 'newest', comparing: true, now: NOW });

      expect(list).toBe(t('count', { count: 5 }));
      // Withdrawn and expired offers can't be compared.
      expect(compared).toBe(t('compareTitle', { count: 3 }));
    });
  }

  it('says when there are no offers yet', () => {
    expect(render('en', { offers: [] })).toContain('No offers yet.');
  });

  it('compares the open offers in a table', () => {
    const markup = render('en', { comparing: true });

    expect(markup).toContain('data-testid="offers-compare"');
    expect(markup).not.toContain('<h1');
    expect(markup).toContain('<th scope="row">Rating</th>');
    // Each offer's column is headed by its total, so screen readers tie cells to an offer.
    expect(markup).toContain(`<th scope="col">${formatMoney(3_136, 'en')}</th>`);
    expect(markup).toContain(`<td>${formatMoney(2_800, 'en')}</td>`);
    expect(markup).toContain('<td aria-hidden="true"></td>');
    expect(markup.match(/>New helper</g)).toHaveLength(3);
    expect(markup).toContain('>Business<');
    expect(markup).toContain('<th scope="row">Business details</th>');
    expect(markup).toContain('Obrt Stjepan Popravci · OIB 00000000698');
    expect(markup).not.toContain(formatMoney(2_688, 'en'));
    expect(markup).toContain('href="/en/ranking"');
    // It opens a new tab, like the other legal links, and says so (review R3-3).
    expect(markup).toMatch(
      /How offers are ranked<span class="[^"]*"> \(opens in a new tab\)<\/span>/,
    );
  });

  it.each(locales)('renders the comparison in %s', (locale) => {
    const text = catalogues[locale].offers;
    const list = render(locale);
    const compare = render(locale, { comparing: true });

    expect(list).toContain(`>${text.compare}</button>`);
    expect(list).toContain(text.trader.private);
    expect(list).toContain(text.obligations.replace(/'/g, '&#x27;'));
    expect(list).toContain(text.trader.contact.replace(/'/g, '&#x27;'));
    expect(list).toContain(text.closed.WITHDRAWN);
    expect(list).toContain(formatMoney(3_000, locale));
    expect(compare).toContain(`>${text.row.price}</th>`);
    expect(compare).toContain(`>${text.backToList}</button>`);
  });
});

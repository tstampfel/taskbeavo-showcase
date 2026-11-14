import { TASK_TIME_ZONE, catalogues, formatMoney, locales, type Locale } from '@taskbeavo/i18n';
import { NextIntlClientProvider } from 'next-intl';
import type { ReactNode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import {
  BookingOutcome,
  DeclineNote,
  OfferCheckoutForm,
  type OfferCheckoutFormProps,
  type Policy,
} from './checkout-parts';

const POLICY_TICK = 'I accept the cancellation policy (version 2026-11-c).';
const EARLY_TICK = 'I ask for the service to start before the withdrawal period ends.';
const TRADER_EARLY_TICK = 'I ask a registered business to start before my withdrawal period ends.';

const policy: Policy = {
  id: 'doc-1',
  kind: 'CANCELLATION',
  version: '2026-11-c',
  language: 'EN',
  effectiveAt: '2026-11-01T00:00:00Z',
  text: 'The full cancellation policy.',
  checkoutConsent: {
    privateHelper: { cancellationPolicy: POLICY_TICK, earlyStart: EARLY_TICK },
    traderHelper: { cancellationPolicy: POLICY_TICK, earlyStart: TRADER_EARLY_TICK },
  },
};

function html(locale: Locale, node: ReactNode) {
  return renderToStaticMarkup(
    <NextIntlClientProvider locale={locale} messages={catalogues[locale]} timeZone={TASK_TIME_ZONE}>
      {node}
    </NextIntlClientProvider>,
  );
}

// Intl separates numbers from units with no-break spaces; compare with plain spaces.
const plain = (text: string) => text.replace(/[\u00a0\u202f]/g, ' ');

function form(locale: Locale, props: Partial<OfferCheckoutFormProps> = {}) {
  return html(
    locale,
    <OfferCheckoutForm
      locale={locale}
      taskTitle="Mow lawn for Mama"
      helperIsTrader={false}
      trader={{ helperBusinessName: null, helperBusinessOib: null, helperBusinessAddress: null }}
      priceCents={3_000}
      feeCents={360}
      feePercent={12}
      feeMinimumCents={250}
      creditCents={0}
      totalCents={3_360}
      policy={policy}
      ticks={{ cancellationPolicy: false, earlyStart: false }}
      onTicks={() => undefined}
      busy={false}
      error={null}
      onPay={() => undefined}
      {...props}
    />,
  );
}

const checkbox = (markup: string, testId: string) =>
  markup.match(new RegExp(`<input[^>]*data-testid="${testId}"[^>]*>`))?.[0] ?? '';
const payButton = (markup: string) =>
  markup.match(/<button[^>]*data-testid="checkout-pay"[^>]*>/)?.[0] ?? '';

describe('OfferCheckoutForm', () => {
  it('shows price, fee and total from the server', () => {
    const markup = form('en');

    expect(markup).toContain(formatMoney(3_000, 'en'));
    expect(markup).toContain(formatMoney(360, 'en'));
    expect(markup).toContain(`Book and pay ${formatMoney(3_360, 'en')}`);
  });

  it('shows the TaskBeavo credit as its own line, and the total with it taken off', () => {
    const markup = plain(form('en', { feeCents: 360, creditCents: 360, totalCents: 3_000 }));

    expect(markup).toContain('data-testid="checkout-credit"');
    expect(markup).toContain('TaskBeavo credit');
    expect(markup).toContain('−€3.60');
    // The notice keeps the fee and its rule; the credit has its own sentence.
    expect(markup).toContain('TaskBeavo service fee €3.60 (12%, min. €2.50) = total €33.60');
    expect(markup).toContain('Your TaskBeavo credit of €3.60 comes off the service fee.');
    expect(markup).toContain('Book and pay €30.00');
  });

  it('shows no credit line without a credit', () => {
    expect(form('en')).not.toContain('checkout-credit');
  });

  it('states the fee notice and the amount due directly above the one order button', () => {
    const markup = plain(form('en', { priceCents: 4_000, feeCents: 480, totalCents: 4_480 }));

    expect(markup).toContain(
      'Task price €40.00 (paid to the helper) + TaskBeavo service fee €4.80 (12%, min. €2.50) = total €44.80',
    );
    const notice = markup.indexOf('checkout-fee-notice');
    const due = markup.indexOf('checkout-total-due');
    const pay = markup.indexOf('data-testid="checkout-pay"');
    expect(notice).toBeGreaterThan(markup.indexOf('checkout-consent'));
    expect(notice).toBeLessThan(due);
    expect(due).toBeLessThan(pay);
    // Nothing between the amount due and the button.
    expect(markup.slice(due, pay)).not.toMatch(/<(p|a|div|fieldset)\b/);
    expect(markup.match(/data-testid="checkout-pay"/g)).toHaveLength(1);
  });

  it('words the notice and the button in Croatian without an amount on the button', () => {
    const markup = plain(form('hr', { priceCents: 4_000, feeCents: 480, totalCents: 4_480 }));

    expect(markup).toContain(
      'Cijena zadatka 40,00 € (plaća se izvršitelju) + naknada za uslugu TaskBeavo 4,80 € (12 %, min. 2,50 €) = ukupno 44,80 €',
    );
    expect(markup).toContain('Ukupno za platiti: 44,80 €');
    expect(markup).toMatch(/data-testid="checkout-pay"[^>]*>Naruči s obvezom plaćanja<\/button>/);
  });

  it.each([
    ['de', 'Zahlungspflichtig buchen'],
    ['fr', 'Commande avec obligation de paiement'],
  ] as const)('labels the %s button with the statutory phrase only', (locale, label) => {
    expect(form(locale)).toMatch(new RegExp(`data-testid="checkout-pay"[^>]*>${label}</button>`));
  });

  it('links the price list above the pay button (BEAV-191)', () => {
    const markup = form('hr');

    expect(markup).toMatch(/href="\/hr\/fees"[^>]*data-testid="checkout-fees"/);
    expect(markup.indexOf('checkout-fees')).toBeLessThan(markup.indexOf('checkout-pay'));
    // ZZP čl. 74(3): the button names the obligation to pay.
    expect(markup).toContain('Naruči s obvezom plaćanja');
  });

  it('words both boxes from the legal document and starts them unticked', () => {
    const markup = form('de');

    expect(markup).toContain(POLICY_TICK);
    expect(markup).toContain(EARLY_TICK);
    expect(checkbox(markup, 'consent-cancellation-policy')).not.toContain('checked');
    expect(checkbox(markup, 'consent-early-start')).not.toContain('checked');
    expect(payButton(markup)).toContain('disabled');
  });

  it.each(locales)('links the cancellation policy page in %s', (locale) => {
    expect(form(locale)).toMatch(
      new RegExp(`<a[^>]*href="/${locale}/cancellation"[^>]*data-testid="policy-page"`),
    );
  });

  it('words the boxes for the offer’s helper type', () => {
    expect(form('en')).not.toContain(TRADER_EARLY_TICK);
    const trader = form('en', { helperIsTrader: true });
    expect(trader).toContain(TRADER_EARLY_TICK);
    expect(trader).not.toContain(EARLY_TICK);
  });

  it('enables Pay only with both boxes ticked', () => {
    expect(
      payButton(form('en', { ticks: { cancellationPolicy: true, earlyStart: false } })),
    ).toContain('disabled');
    expect(
      payButton(form('en', { ticks: { cancellationPolicy: true, earlyStart: true } })),
    ).not.toContain('disabled');
  });

  it('without the lawyer’s tick-box wording, says checkout is unavailable', () => {
    const markup = form('fr', {
      policy: { ...policy, checkoutConsent: null },
      ticks: { cancellationPolicy: true, earlyStart: true },
    });

    expect(markup).toContain('data-testid="policy-unavailable"');
    expect(markup).toContain(catalogues.fr.checkout.policy.unavailable.replace(/'/g, '&#x27;'));
    expect(payButton(markup)).toContain('disabled');
  });

  it('names a trader helper’s business, OIB and registered address next to the label', () => {
    const trader = form('en', {
      helperIsTrader: true,
      trader: {
        helperBusinessName: 'Vrtlarski obrt Horvat',
        helperBusinessOib: '00000000698',
        helperBusinessAddress: 'Obala Stjepana Radića 12, 20000 Dubrovnik, HR',
      },
    });

    expect(trader).toContain('Vrtlarski obrt Horvat · OIB 00000000698');
    expect(trader).toContain(
      'Registered business address: Obala Stjepana Radića 12, 20000 Dubrovnik, HR',
    );
    expect(trader).toContain('Contact: in-app chat');
    expect(form('en')).not.toContain('data-testid="trader-identity"');
  });

  it('explains the consumer rules by helper type', () => {
    expect(form('en')).toContain(catalogues.en.checkout.helper.private.replace(/'/g, '&#x27;'));
    expect(form('en', { helperIsTrader: true })).toContain(
      catalogues.en.checkout.helper.business.replace(/'/g, '&#x27;'),
    );
  });

  it('opens with one booking summary card: avatar, task, helper type, then the amounts', () => {
    const markup = form('en');
    const summary = markup.split('data-testid="checkout-summary"')[1]!;

    expect(markup.match(/data-testid="checkout-summary"/g)).toHaveLength(1);
    expect(summary.indexOf('data-testid="helper-avatar"')).toBeGreaterThan(-1);
    expect(summary.indexOf('Mow lawn for Mama')).toBeLessThan(
      summary.indexOf('Private individual'),
    );
    expect(summary.indexOf('Private individual')).toBeLessThan(summary.indexOf('Agreed price'));
  });

  it('keeps a trader helper’s business details inside the summary card', () => {
    const markup = form('en', {
      helperIsTrader: true,
      trader: {
        helperBusinessName: 'Vrtlarski obrt Horvat',
        helperBusinessOib: '00000000698',
        helperBusinessAddress: 'Obala Stjepana Radića 12, 20000 Dubrovnik, HR',
      },
    });
    const summary = markup.split('data-testid="checkout-summary"')[1]!;

    expect(summary.indexOf('Vrtlarski obrt Horvat · OIB 00000000698')).toBeLessThan(
      summary.indexOf('Agreed price'),
    );
  });

  it.each(locales)(
    'says the payment is held via Stripe, right under the summary, in %s',
    (locale) => {
      const markup = form(locale);
      const secure = markup.indexOf('data-testid="checkout-secure"');

      expect(secure).toBeGreaterThan(markup.indexOf('data-testid="checkout-summary"'));
      expect(markup.slice(secure).split('</p>')[0]).toContain(catalogues[locale].checkout.secure);
      expect(catalogues[locale].checkout.secure).toContain('Stripe');
    },
  );

  it('pins the fee notice, the amount due and the order button in one footer bar', () => {
    const markup = form('en');
    const foot = markup.split('data-testid="checkout-foot"')[1]!;

    expect(foot).toContain('data-testid="checkout-fee-notice"');
    expect(foot).toContain('data-testid="checkout-pay"');
    // The consent boxes stay above it, in the page.
    expect(foot).not.toContain('checkout-consent');
  });

  it('shows the server’s error code in words', () => {
    expect(form('en', { error: 'errors.OFFER_CHANGED' })).toContain(
      catalogues.en.errors.OFFER_CHANGED.replace(/'/g, '&#x27;'),
    );
  });
});

describe('BookingOutcome', () => {
  const booking = { id: 'b1', amountCents: 3_000, feeCents: 360, totalCents: 3_360 };
  const outcome = (locale: Locale, kind: Parameters<typeof BookingOutcome>[0]['view']['kind']) =>
    html(
      locale,
      <BookingOutcome
        booking={booking}
        view={{ kind } as Parameters<typeof BookingOutcome>[0]['view']}
        locale={locale}
        onBackToOffers={() => undefined}
        onViewBooking={() => undefined}
      />,
    );

  it.each(locales)('in %s, confirms without claiming the money is held yet', (locale) => {
    const markup = outcome(locale, 'confirming');

    expect(markup).toContain('data-testid="checkout-confirming"');
    expect(markup).not.toContain('data-testid="checkout-held"');
  });

  it('says held, notified and emailed once the server reports FUNDS_HELD', () => {
    const markup = outcome('en', 'held');

    expect(markup).toContain(`${formatMoney(3_360, 'en')} held safely via Stripe`);
    expect(markup).toContain(catalogues.en.checkout.held.notified);
    expect(markup).toContain('emailed your booking confirmation');
  });

  it.each([
    ['expired', 'checkout-expired'],
    ['withdrawn', 'checkout-withdrawn'],
    ['notStarted', 'checkout-not-started'],
    ['authenticating', 'checkout-authenticating'],
  ] as const)('shows the %s state', (kind, testId) => {
    expect(outcome('hr', kind)).toContain(`data-testid="${testId}"`);
  });
});

describe('DeclineNote', () => {
  it.each(locales)('in %s, says why and that nothing was charged', (locale) => {
    const markup = html(locale, <DeclineNote decline="insufficient_funds" />);
    const messages = catalogues[locale].checkout;

    expect(markup).toContain(messages.declined.nothingCharged.replace(/'/g, '&#x27;'));
    expect(markup).toContain(messages.decline.insufficient_funds.replace(/'/g, '&#x27;'));
  });
});

describe('checkout copy', () => {
  it.each(locales)('never says TaskBeavo holds the money in %s', (locale) => {
    expect(JSON.stringify(catalogues[locale].checkout).toLowerCase()).not.toMatch(
      /held by taskbeavo|taskbeavo holds|hält taskbeavo|von taskbeavo (gehalten|verwahrt)|détenu par taskbeavo|taskbeavo drži|drži taskbeavo/,
    );
  });

  it.each(locales)('never calls the held money "escrow" in %s', (locale) => {
    expect(JSON.stringify(catalogues[locale].checkout).toLowerCase()).not.toMatch(
      /escrow|treuhand|séquestre|sequestre/,
    );
  });
});

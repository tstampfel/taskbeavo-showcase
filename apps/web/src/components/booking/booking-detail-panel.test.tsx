import { bookingDetailView, type BookingDetail } from '@taskbeavo/graphql';
import { TASK_TIME_ZONE, catalogues, formatMoney, locales, type Locale } from '@taskbeavo/i18n';
import { NextIntlClientProvider } from 'next-intl';
import type { ReactNode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { BookingDetailPanel } from './booking-detail-panel';

vi.mock('../../i18n/navigation', () => ({
  Link: ({ href, children, ...props }: { href: string; children: ReactNode }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

const ADDRESS = 'Ulica Ante Starčevića 24, Gruž';
const NOW = new Date('2026-11-09T10:00:00Z');

type Booking = Parameters<typeof bookingDetailView>[0];

function booking(extra: Partial<Booking> = {}, exact = true): Booking {
  return {
    helperUserId: 'h',
    status: 'FUNDS_HELD',
    checkoutExpiresAt: null,
    lastPaymentAttempt: null,
    checkoutFailureReason: null,
    task: {
      id: 't1',
      title: 'Grocery run for Mama',
      category: null,
      serviceAreaName: 'Dubrovnik',
      address: exact ? ADDRESS : null,
      accessDetails: exact ? 'Gate 4417' : null,
      onSiteContact: exact
        ? { id: 'c1', name: 'Mama Kata', area: 'Gruž', language: 'HR', phone: '+385912345678' }
        : null,
    },
    helperBusiness: null,
    cancelPreview: null,
    helperCancelPreview: null,
    withdrawalAvailable: false,
    canRequestNoFault: false,
    noFaultRequest: null,
    cancellation: null,
    arrival: { id: 'b1', noShowContestBy: null },
    ...extra,
  };
}

function render(locale: Locale, b: Booking, role: 'poster' | 'helper') {
  const view = bookingDetailView(b, role, NOW) as { kind: 'detail' } & BookingDetail;
  return renderToStaticMarkup(
    <NextIntlClientProvider locale={locale} messages={catalogues[locale]} timeZone={TASK_TIME_ZONE}>
      <BookingDetailPanel
        bookingId="b1"
        view={view}
        title={b.task.title}
        slotStart="2026-11-14T08:00:00Z"
        amountCents={role === 'poster' ? 3_360 : 3_000}
        locale={locale}
      />
    </NextIntlClientProvider>,
  );
}

const html = (text: string) =>
  text.replaceAll('&', '&amp;').replaceAll("'", '&#x27;').replaceAll('"', '&quot;');

describe('BookingDetailPanel', () => {
  it.each(locales)('shows the active helper the exact place in %s', (locale) => {
    const out = render(locale, booking(), 'helper');
    const t = catalogues[locale].bookingDetail;
    expect(out).toContain(ADDRESS);
    expect(out).toContain(html(t.place.whileBooked));
    expect(out).toContain('href="tel:+385912345678"');
    expect(out).toContain(html(t.status.FUNDS_HELD));
    expect(out).toContain(formatMoney(3_000, locale));
    // Who the poster contracts with is the poster's.
    expect(out).not.toContain('data-testid="booking-helper"');
  });

  it('shows only the area once the server stops sending the address', () => {
    const out = render('en', booking({ status: 'CLOSED' }, false), 'helper');
    expect(out).not.toContain(ADDRESS);
    expect(out).not.toContain('Gate 4417');
    expect(out).not.toContain('Mama Kata');
    expect(out).toContain('Area: Dubrovnik');
    expect(out).toContain(html(catalogues.en.bookingDetail.place.ended));
  });

  it('marks the current step of the stepper', () => {
    const out = render('en', booking({ status: 'PAYMENT_REQUESTED' }), 'poster');
    expect(out).toContain('aria-current="step" data-testid="booking-step-requested-current"');
    expect(out).toContain('data-testid="booking-step-held-done"');
  });

  it('links the poster to release, and never to the helper-only pages', () => {
    const out = render('en', booking({ status: 'PAYMENT_REQUESTED' }), 'poster');
    expect(out).toContain('href="/bookings/b1/release"');
    expect(out).not.toContain('href="/bookings/b1/arrival"');
    expect(out).not.toContain('href="/bookings/b1/no-access"');
  });

  it('links both sides to the day of the job while the money is held', () => {
    expect(render('en', booking(), 'poster')).toContain('href="/bookings/b1/arrival"');
    expect(render('en', booking(), 'helper')).toContain('href="/bookings/b1/arrival"');
  });

  it("shows the poster a trader helper's name and OIB as confirmed at checkout", () => {
    const out = render(
      'de',
      booking({
        helperBusiness: {
          id: 'b1',
          businessName: 'Vrtlarski obrt Horvat',
          businessOib: '00000000124',
        },
      }),
      'poster',
    );
    expect(out).toContain('Vrtlarski obrt Horvat · OIB 00000000124');
    // The registered address stays on the offer and the checkout (hard rule e).
    expect(out).not.toContain(html(catalogues.de.offers.trader.address.replace('{address}', '')));
    expect(out).toContain(html(catalogues.de.checkout.held.email));
  });

  it('shows a private helper as a private individual', () => {
    expect(render('fr', booking(), 'poster')).toContain(html(catalogues.fr.offers.trader.private));
  });

  it('shows the poster the last card decline and the way back to pay', () => {
    const out = render(
      'en',
      booking(
        {
          status: 'AWAITING_PAYMENT',
          checkoutExpiresAt: '2026-11-09T10:20:00Z',
          lastPaymentAttempt: {
            id: 'a1',
            kind: 'DECLINED',
            declineCode: 'expired_card',
            at: '2026-11-09T09:55:00Z',
          },
        },
        false,
      ),
      'poster',
    );
    expect(out).toContain(html(catalogues.en.checkout.decline.expired_card));
    expect(out).toContain('href="/bookings/b1/checkout"');
  });

  // BEAV-212: aligned with mockups 7b and 18a.
  const section = (out: string, testId: string) => {
    const start = out.indexOf(`data-testid="${testId}"`);
    expect(start, testId).toBeGreaterThan(-1);
    return out.slice(start);
  };

  it.each(locales)('opens with one summary card: date, task and amount line, in %s', (locale) => {
    const t = catalogues[locale].bookingDetail;
    const poster = section(render(locale, booking(), 'poster'), 'booking-summary');
    expect(poster).toContain('Grocery run for Mama');
    expect(poster).toContain(html(t.amountLabelPoster));
    expect(poster).toContain(formatMoney(3_360, locale));
    const helper = section(render(locale, booking(), 'helper'), 'booking-summary');
    expect(helper).toContain(html(t.amountLabelHelper));
    expect(helper).toContain(formatMoney(3_000, locale));
  });

  it.each(locales)(
    'shows "Payment requested" as its own card for both sides (7b), in %s',
    (locale) => {
      const t = catalogues[locale].bookingDetail;
      const requested = booking({ status: 'PAYMENT_REQUESTED' });
      const helper = section(render(locale, requested, 'helper'), 'booking-requested');
      expect(helper).toContain(html(t.status.PAYMENT_REQUESTED));
      expect(helper).toContain(
        html(t.requestedHelper.replace('{amount}', formatMoney(3_000, locale))),
      );
      const poster = section(render(locale, requested, 'poster'), 'booking-requested');
      expect(poster).toContain(html(t.requestedPoster));
      expect(render(locale, booking(), 'helper')).not.toContain('booking-requested');
    },
  );

  it('gives the on-site contact its own card with a Call button (7b, 18a), never without the exact place', () => {
    const t = catalogues.en.bookingDetail.place;
    const contact = section(render('en', booking(), 'helper'), 'booking-contact');
    expect(contact).toContain(
      html(t.contact.replace('{name}', 'Mama Kata').replace('{area}', 'Gruž')),
    );
    expect(contact).toMatch(/<a [^>]*href="tel:\+385912345678"[^>]*data-testid="booking-call"/);
    // The poster owns the contact and sees its phone too.
    expect(section(render('en', booking(), 'poster'), 'booking-contact')).toContain(
      'href="tel:+385912345678"',
    );
    expect(render('en', booking({ status: 'CLOSED' }, false), 'helper')).not.toContain(
      'booking-contact',
    );
  });

  it('lists the access details under their own label, only with the exact place', () => {
    const t = catalogues.de.bookingDetail.place;
    const out = render('de', booking(), 'helper');
    expect(section(out, 'booking-access')).toContain('Gate 4417');
    expect(out).toContain(html(t.accessTitle));
    expect(render('de', booking({}, false), 'helper')).not.toContain(html(t.accessTitle));
  });

  it.each(['RELEASED', 'PAID_OUT'] as const)(
    'never says the money is still held once it is %s',
    (status) => {
      const t = catalogues.en.bookingDetail;
      const out = render('en', booking({ status }), 'poster');
      expect(out).not.toContain(html(t.heldPoster.replace('{total}', formatMoney(3_360, 'en'))));
      expect(render('en', booking({ status: 'PAYMENT_REQUESTED' }), 'poster')).toContain(
        html(t.heldPoster.replace('{total}', formatMoney(3_360, 'en'))),
      );
    },
  );

  // Review R1-2: under the shell's h1, no h3 may come before the first h2.
  it.each(['FUNDS_HELD', 'PAYMENT_REQUESTED'] as const)(
    'keeps the heading order on a %s booking',
    (status) => {
      for (const role of ['poster', 'helper'] as const) {
        const levels = [...render('en', booking({ status }), role).matchAll(/<h([1-6])/g)].map(
          (m) => Number(m[1]),
        );
        expect(levels[0], `${status} ${role}`).toBe(2);
      }
    },
  );

  it('puts the safety line in a note', () => {
    expect(section(render('fr', booking(), 'poster'), 'booking-safety')).toContain(
      html(catalogues.fr.bookingDetail.safety),
    );
  });

  it('tells the helper the address comes with the payment, without the card attempts', () => {
    const out = render(
      'hr',
      booking({ status: 'AWAITING_PAYMENT', checkoutExpiresAt: '2026-11-09T10:20:00Z' }, false),
      'helper',
    );
    const t = catalogues.hr.bookingDetail;
    expect(out).toContain(html(t.place.afterPayment));
    expect(out).toContain(html(t.checkout.waiting));
    expect(out).not.toContain('booking-pay');
  });
});

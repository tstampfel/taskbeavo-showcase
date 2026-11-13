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

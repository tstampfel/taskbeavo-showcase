import { arrivalView } from '@taskbeavo/graphql';
import { TASK_TIME_ZONE, catalogues, formatMoney, locales, type Locale } from '@taskbeavo/i18n';
import { NextIntlClientProvider } from 'next-intl';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { ArrivalPanel, type ArrivalPanelProps } from './arrival-panel';

function booking(helperUserId: string, extra: Record<string, unknown> = {}) {
  return {
    helperUserId,
    arrival: {
      id: 'b1',
      onMyWayAt: null,
      etaAt: null,
      lateReportedAt: null,
      lateAcceptedAt: null,
      arrivedAt: null,
      posterConfirmedCameAt: null,
      noShowConfirmedAt: null,
      onTheWayPromptSentAt: null,
      posterAskedAt: null,
      noShowDueAt: null,
      noShowContestBy: null,
      noShowContestedAt: null,
      noShowSettledAt: null,
      noShowAwaitingSupport: false,
      noShowRejected: false,
      noShowRefundCents: null,
      noShowCreditCents: null,
      earlyStartRequestedAt: null,
      earlyStartConfirmedAt: null,
      earlyStartDeclinedAt: null,
      earlyStartPending: false,
      canTap: false,
      canRequestEarlyStart: false,
      canAnswerLate: false,
      canConfirmCame: false,
      canConfirmNoShow: false,
      canContestNoShow: false,
      canAnswerEarlyStart: false,
      earlyStartWithdrawal: false,
      ...extra,
    },
  } as Parameters<typeof arrivalView>[0];
}

const actions = {
  onMyWay: vi.fn(),
  onLate: vi.fn(),
  onArrived: vi.fn(),
  onCantCome: vi.fn(),
  onStartEarly: vi.fn(),
  onContest: vi.fn(),
  onAnswerLate: vi.fn(),
  onConfirmCame: vi.fn(),
  onAnswerEarly: vi.fn(),
};

function render(locale: Locale, b: ReturnType<typeof booking>, viewer: string) {
  const view = arrivalView(b, b.helperUserId === viewer ? 'helper' : 'poster');
  return renderToStaticMarkup(
    <NextIntlClientProvider locale={locale} messages={catalogues[locale]} timeZone={TASK_TIME_ZONE}>
      <ArrivalPanel
        view={view as ArrivalPanelProps['view']}
        locale={locale}
        busy={false}
        actions={actions}
      />
    </NextIntlClientProvider>,
  );
}

const html = (text: string) =>
  text.replaceAll('&', '&amp;').replaceAll("'", '&#x27;').replaceAll('"', '&quot;');

describe('ArrivalPanel', () => {
  it.each(locales)('offers the helper the taps in %s', (locale) => {
    const out = render(locale, booking('h', { canTap: true }), 'h');
    const t = catalogues[locale].arrival;
    expect(out).toContain('data-testid="arrival-on-my-way"');
    expect(out).toContain('data-testid="arrival-arrived"');
    expect(out).toContain(html(t.cantCome));
    expect(out).toContain(html(t.statusHelper.none));
  });

  it('asks the poster whether the helper came, without the No warning until asked', () => {
    const out = render(
      'en',
      booking('h', { canConfirmCame: true, canConfirmNoShow: true, noShowRefundCents: 3_360 }),
      'p',
    );
    expect(out).toContain('data-testid="arrival-came-yes"');
    expect(out).toContain('data-testid="arrival-came-no"');
    expect(out).not.toContain('data-testid="arrival-no-show-confirm"');
    expect(out).not.toContain(formatMoney(3_360, 'en'));
  });

  it('shows the poster where a reported no-show stands, and never the helper buttons', () => {
    const out = render(
      'de',
      booking('h', {
        noShowContestBy: '2026-11-09T10:00:00Z',
        noShowContestedAt: '2026-11-08T12:00:00Z',
        noShowAwaitingSupport: true,
      }),
      'p',
    );
    expect(out).toContain(html(catalogues.de.arrival.contestedPoster));
    expect(out).not.toContain('arrival-contest');
    expect(out).not.toContain('arrival-on-my-way');
  });

  it('gives the helper "I was there" inside the 24 hours', () => {
    const out = render(
      'hr',
      booking('h', { noShowContestBy: '2026-11-09T10:00:00Z', canContestNoShow: true }),
      'h',
    );
    expect(out).toContain('data-testid="arrival-contest"');
    expect(out).toContain(html(catalogues.hr.arrival.contest));
  });

  it.each(locales)(
    'keeps the withdrawal right in the early-start copy only when the server says so (%s)',
    (locale) => {
      const pending = { earlyStartPending: true, canAnswerEarlyStart: true };
      const t = catalogues[locale].arrival;
      const trader = render(locale, booking('h', { ...pending, earlyStartWithdrawal: true }), 'p');
      expect(trader).toContain(html(t.earlyStartBodyWithdrawal));
      const privateHelper = render(locale, booking('h', pending), 'p');
      expect(privateHelper).toContain(html(t.earlyStartBody));
      expect(privateHelper).not.toContain(html(t.earlyStartBodyWithdrawal));
    },
  );

  it('offers the late answer to the poster', () => {
    const out = render('fr', booking('h', { canAnswerLate: true }), 'p');
    expect(out).toContain(html(catalogues.fr.arrival.lateCancel));
    expect(out).toContain(html(catalogues.fr.arrival.lateKeep));
  });
});

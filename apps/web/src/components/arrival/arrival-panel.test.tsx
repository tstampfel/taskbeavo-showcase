import { arrivalView } from '@taskbeavo/graphql';
import {
  TASK_TIME_ZONE,
  catalogues,
  formatMoney,
  formatTime,
  locales,
  type Locale,
} from '@taskbeavo/i18n';
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
        summary={{ title: 'Grocery run for Mama', slotStart: '2026-11-08T08:00:00Z' }}
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

  // BEAV-202: On my way without an ETA used to read "arriving" with no time.
  const setOff = '2026-11-08T09:42:00Z';
  const eta = '2026-11-08T10:05:00Z';
  const status = (out: string) => /data-testid="arrival-status">([^<]*)</.exec(out)?.[1] ?? '';

  it.each(locales)('shows the poster the ETA when the helper gave one, in %s', (locale) => {
    const out = render(locale, booking('h', { onMyWayAt: setOff, etaAt: eta }), 'p');
    const expected = catalogues[locale].arrival.statusPoster.onMyWay.replace(
      '{time}',
      formatTime(eta, locale),
    );
    expect(status(out)).toBe(html(expected));
  });

  it.each(locales)('shows when the helper set off when there is no ETA, in %s', (locale) => {
    for (const viewer of ['p', 'h']) {
      const out = render(locale, booking('h', { onMyWayAt: setOff }), viewer);
      const side = viewer === 'h' ? 'statusHelper' : 'statusPoster';
      const expected = catalogues[locale].arrival[side].onMyWayNoEta.replace(
        '{time}',
        formatTime(setOff, locale),
      );
      expect(status(out)).toBe(html(expected));
      expect(status(out)).toContain(formatTime(setOff, locale));
    }
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

  // BEAV-212: aligned with mockup 18 (18a, 18c-18i).
  const part = (out: string, testId: string) => {
    const start = out.indexOf(`data-testid="${testId}"`);
    expect(start, testId).toBeGreaterThan(-1);
    return out.slice(start);
  };

  it.each(locales)('opens with the slot, the status and the task in one card, in %s', (locale) => {
    for (const viewer of ['h', 'p']) {
      const summary = part(render(locale, booking('h'), viewer), 'arrival-summary');
      expect(summary).toContain('Grocery run for Mama');
      expect(summary).toContain('data-testid="arrival-status"');
    }
  });

  it.each(locales)(
    "before setting off, the bar holds On my way and I've arrived (18a), in %s",
    (locale) => {
      const t = catalogues[locale].arrival;
      const out = render(locale, booking('h', { canTap: true }), 'h');
      const foot = part(out, 'arrival-foot');
      expect(foot).toContain('data-testid="arrival-on-my-way"');
      expect(foot).toContain('data-testid="arrival-arrived"');
      expect(out).toContain(html(t.etaLegend));
      expect(out).toContain(html(t.onMyWayHint));
      expect(out).toContain('data-testid="arrival-running-late"');
    },
  );

  it.each(locales)('once on the way, the chips send a new arrival time (18g), in %s', (locale) => {
    const t = catalogues[locale].arrival;
    const out = render(
      locale,
      booking('h', {
        canTap: true,
        onMyWayAt: '2026-11-08T07:40:00Z',
        etaAt: '2026-11-08T08:15:00Z',
      }),
      'h',
    );
    expect(out).toContain(html(t.runningLate));
    expect(out).toContain(html(t.lateHint));
    expect(out).toContain('data-testid="arrival-eta-30"');
    expect(part(out, 'arrival-foot')).toMatch(
      /<button[^>]*disabled=""[^>]*data-testid="arrival-send-late"/,
    );
    expect(out).not.toContain('arrival-on-my-way');
    expect(out).not.toContain('arrival-running-late');
  });

  it('explains what support looks at next to "I was there" (18h)', () => {
    const out = render(
      'en',
      booking('h', { noShowContestBy: '2026-11-09T10:00:00Z', canContestNoShow: true }),
      'h',
    );
    expect(part(out, 'arrival-no-show')).toContain(html(catalogues.en.arrival.contestExplainer));
  });

  it.each(locales)(
    'badges where a reported no-show stands for the poster (18e, 18f), in %s',
    (locale) => {
      const t = catalogues[locale].arrival.noShowBadge;
      const by = { noShowContestBy: '2026-11-09T10:00:00Z' };
      expect(part(render(locale, booking('h', by), 'p'), 'arrival-no-show')).toContain(
        html(t.reported),
      );
      expect(
        part(
          render(locale, booking('h', { ...by, noShowContestedAt: '2026-11-08T12:00:00Z' }), 'p'),
          'arrival-no-show',
        ),
      ).toContain(html(t.contested));
      expect(
        part(
          render(locale, booking('h', { ...by, noShowSettledAt: '2026-11-09T10:01:00Z' }), 'p'),
          'arrival-no-show',
        ),
      ).toContain(html(t.settled));
    },
  );

  it('gives the poster no button bar: every answer sits with its question', () => {
    const out = render('en', booking('h', { canAnswerLate: true, canConfirmCame: true }), 'p');
    expect(out).not.toContain('arrival-foot');
    expect(part(out, 'arrival-late')).toContain('data-testid="arrival-late-keep"');
    expect(part(out, 'arrival-did-they-come')).toContain('data-testid="arrival-came-yes"');
  });
});

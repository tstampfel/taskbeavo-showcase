import { describe, expect, it } from 'vitest';
import {
  MIN_TIP_CENTS,
  TIP_CHOICES_CENTS,
  parseEuroCents,
  partialReleaseCheck,
  releaseSummary,
  releaseView,
  tipChoices,
  tipCheck,
  type ReleaseBooking,
} from './release';

const base: ReleaseBooking = {
  status: 'PAYMENT_REQUESTED',
  amountCents: 3000,
  canReleasePayment: true,
  releaseProposal: null,
  tip: null,
};

const proposal = (status: 'OPEN' | 'ACCEPTED' | 'DECLINED' | 'WITHDRAWN' | 'CLOSED') => ({
  id: 'p1',
  releasedCents: 2000,
  status,
  refundCents: 1120,
  createdAt: '2026-11-07T11:10:00Z',
  answeredAt: null,
});

describe('tips', () => {
  it('offers €2, €5 and €10 up to the task price', () => {
    expect(TIP_CHOICES_CENTS).toEqual([200, 500, 1000]);
    expect(tipChoices(3000)).toEqual([200, 500, 1000]);
    expect(tipChoices(600)).toEqual([200, 500]);
    expect(tipChoices(150)).toEqual([]);
  });

  it('takes €1 up to the task price in whole cents, as the server does', () => {
    expect(MIN_TIP_CENTS).toBe(100);
    expect(tipCheck(100, 3000)).toBe(true);
    expect(tipCheck(3000, 3000)).toBe(true);
    expect(tipCheck(99, 3000)).toBe(false);
    expect(tipCheck(3001, 3000)).toBe(false);
    expect(tipCheck(150.5, 3000)).toBe(false);
  });

  it('sums the release and the tip for the confirmation', () => {
    expect(releaseSummary(3000, null)).toEqual({ priceCents: 3000, tipCents: 0, totalCents: 3000 });
    expect(releaseSummary(3000, 500)).toEqual({
      priceCents: 3000,
      tipCents: 500,
      totalCents: 3500,
    });
  });
});

describe('parseEuroCents', () => {
  it.each([
    ['20', 2000],
    ['20.5', 2050],
    ['20,50', 2050],
    [' 7,05 ', 705],
    ['€ 12', 1200],
    ['0,99', 99],
  ])('reads %j as %i cents', (text, cents) => expect(parseEuroCents(text)).toBe(cents));

  it.each(['', 'abc', '20.555', '1.000,00', '-5', '2 0'])('refuses %j', (text) =>
    expect(parseEuroCents(text)).toBeNull(),
  );
});

describe('partialReleaseCheck', () => {
  it('takes more than nothing and less than the price', () => {
    expect(partialReleaseCheck('20', 3000)).toEqual({ ok: true, releasedCents: 2000 });
    expect(partialReleaseCheck('0,01', 3000)).toEqual({ ok: true, releasedCents: 1 });
    expect(partialReleaseCheck('29,99', 3000)).toEqual({ ok: true, releasedCents: 2999 });
  });

  it('says what is wrong otherwise', () => {
    expect(partialReleaseCheck('', 3000)).toEqual({ ok: false, problem: 'format' });
    expect(partialReleaseCheck('0', 3000)).toEqual({ ok: false, problem: 'range' });
    expect(partialReleaseCheck('30', 3000)).toEqual({ ok: false, problem: 'range' });
    expect(partialReleaseCheck('45', 3000)).toEqual({ ok: false, problem: 'range' });
  });
});

describe('releaseView for the poster', () => {
  it('offers release, the tip and the lower amount when the server allows it', () => {
    expect(releaseView(base, 'poster')).toEqual({
      kind: 'release',
      priceCents: 3000,
      declined: null,
      tipChoices: [200, 500, 1000],
    });
  });

  it('says the helper declined the last proposal', () => {
    const view = releaseView({ ...base, releaseProposal: proposal('DECLINED') }, 'poster');
    expect(view).toMatchObject({ kind: 'release', declined: { releasedCents: 2000 } });
  });

  it('shows the open proposal, which the poster may withdraw or overrule by releasing', () => {
    expect(releaseView({ ...base, releaseProposal: proposal('OPEN') }, 'poster')).toEqual({
      kind: 'proposalOpen',
      priceCents: 3000,
      releasedCents: 2000,
      refundCents: 1120,
      canRelease: true,
    });
  });

  it('shows an accepted proposal as settling, then settled', () => {
    expect(
      releaseView(
        { ...base, canReleasePayment: false, releaseProposal: proposal('ACCEPTED') },
        'poster',
      ),
    ).toEqual({ kind: 'partialSettled', releasedCents: 2000, refundCents: 1120, done: false });
    expect(
      releaseView(
        {
          ...base,
          status: 'PARTIALLY_SETTLED',
          canReleasePayment: false,
          releaseProposal: proposal('ACCEPTED'),
        },
        'poster',
      ),
    ).toMatchObject({ kind: 'partialSettled', done: true });
  });

  it('after a full release, offers a tip until one is paid', () => {
    const released = { ...base, status: 'RELEASED' as const, canReleasePayment: false };
    expect(releaseView(released, 'poster')).toEqual({
      kind: 'released',
      priceCents: 3000,
      tip: { kind: 'none', choices: [200, 500, 1000] },
    });
    expect(
      releaseView(
        { ...released, status: 'PAID_OUT', tip: { id: 't', amountCents: 500, status: 'PENDING' } },
        'poster',
      ),
    ).toMatchObject({ tip: { kind: 'pending', amountCents: 500 } });
    expect(
      releaseView(
        { ...released, tip: { id: 't', amountCents: 500, status: 'SUCCEEDED' } },
        'poster',
      ),
    ).toMatchObject({ tip: { kind: 'paid', amountCents: 500 } });
  });

  it('closes the tip once the booking is closed', () => {
    expect(
      releaseView({ ...base, status: 'CLOSED', canReleasePayment: false }, 'poster'),
    ).toMatchObject({ kind: 'released', tip: { kind: 'closed' } });
  });

  it('says why release is not offered otherwise', () => {
    expect(
      releaseView({ ...base, status: 'FUNDS_HELD', canReleasePayment: false }, 'poster'),
    ).toEqual({ kind: 'notRequested' });
    expect(releaseView({ ...base, canReleasePayment: false }, 'poster')).toEqual({
      kind: 'unavailable',
    });
    expect(
      releaseView({ ...base, status: 'DISPUTED', canReleasePayment: false }, 'poster'),
    ).toEqual({ kind: 'unavailable' });
  });
});

describe('releaseView for the helper', () => {
  it('asks the helper to answer an open proposal', () => {
    expect(
      releaseView(
        {
          ...base,
          canReleasePayment: false,
          releaseProposal: { ...proposal('OPEN'), refundCents: null },
        },
        'helper',
      ),
    ).toEqual({ kind: 'answer', priceCents: 3000, releasedCents: 2000 });
  });

  it('otherwise says where the payment stands', () => {
    expect(releaseView({ ...base, canReleasePayment: false }, 'helper')).toEqual({
      kind: 'helperWaiting',
      priceCents: 3000,
    });
    expect(
      releaseView(
        {
          ...base,
          status: 'PAID_OUT',
          canReleasePayment: false,
          tip: { id: 't', amountCents: 500, status: 'SUCCEEDED' },
        },
        'helper',
      ),
    ).toEqual({ kind: 'helperReleased', priceCents: 3000, tipCents: 500 });
    expect(
      releaseView({ ...base, status: 'FUNDS_HELD', canReleasePayment: false }, 'helper'),
    ).toEqual({ kind: 'unavailable' });
  });

  it('never shows a page to an unknown viewer', () => {
    expect(releaseView(base, 'unknown')).toEqual({ kind: 'unknownViewer' });
  });
});

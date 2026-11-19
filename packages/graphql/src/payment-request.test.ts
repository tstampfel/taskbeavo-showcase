import { describe, expect, it } from 'vitest';
import {
  PAYMENT_REQUEST_MAX_PHOTOS,
  PAYMENT_REQUEST_NOTE_MAX,
  isJir,
  paymentRequestInput,
  type PaymentRequestDraft,
} from './payment-request';

const JIR = '6b8a3c2e-1f4d-4a7b-9c0e-5d2f8a1b3c4d';
const empty: PaymentRequestDraft = {
  note: '',
  photoIds: [],
  invoiceUploadId: null,
  invoiceJir: '',
};

describe('isJir', () => {
  it('takes the 8-4-4-4-12 form in either case, trimmed', () => {
    expect(isJir(JIR)).toBe(true);
    expect(isJir(` ${JIR.toUpperCase()} `)).toBe(true);
  });

  it.each(['', 'not-a-jir', JIR.replace(/-/g, ''), `${JIR}0`, '12345678901'])(
    'refuses %j',
    (value) => expect(isJir(value)).toBe(false),
  );
});

describe('paymentRequestInput', () => {
  it('sends a private helper’s photos and trimmed note, both optional', () => {
    expect(paymentRequestInput('b1', empty, false)).toEqual({
      ok: true,
      input: { bookingId: 'b1', note: null, photoIds: null },
    });
    expect(
      paymentRequestInput('b1', { ...empty, note: '  Done  ', photoIds: ['p1', 'p2'] }, false),
    ).toEqual({ ok: true, input: { bookingId: 'b1', note: 'Done', photoIds: ['p1', 'p2'] } });
  });

  it('never sends an invoice for a private helper', () => {
    const check = paymentRequestInput('b1', { ...empty, invoiceJir: JIR }, false);
    expect(check).toEqual({ ok: true, input: { bookingId: 'b1', note: null, photoIds: null } });
  });

  it('asks a registered business for the invoice first', () => {
    expect(paymentRequestInput('b1', empty, true)).toEqual({
      ok: false,
      problem: 'invoiceMissing',
    });
  });

  it('takes the invoice upload, or a JIR in the right format', () => {
    expect(paymentRequestInput('b1', { ...empty, invoiceUploadId: 'u1' }, true)).toEqual({
      ok: true,
      input: { bookingId: 'b1', note: null, photoIds: null, invoiceUploadId: 'u1' },
    });
    expect(paymentRequestInput('b1', { ...empty, invoiceJir: ` ${JIR} ` }, true)).toEqual({
      ok: true,
      input: { bookingId: 'b1', note: null, photoIds: null, invoiceJir: JIR },
    });
    expect(paymentRequestInput('b1', { ...empty, invoiceJir: 'abc' }, true)).toEqual({
      ok: false,
      problem: 'jirInvalid',
    });
  });

  it('caps the note and the photos', () => {
    const note = 'x'.repeat(PAYMENT_REQUEST_NOTE_MAX + 1);
    expect(paymentRequestInput('b1', { ...empty, note }, false)).toEqual({
      ok: false,
      problem: 'noteTooLong',
    });
    const photoIds = Array.from({ length: PAYMENT_REQUEST_MAX_PHOTOS + 1 }, (_, i) => `p${i}`);
    expect(paymentRequestInput('b1', { ...empty, photoIds }, false)).toEqual({
      ok: false,
      problem: 'tooManyPhotos',
    });
  });
});

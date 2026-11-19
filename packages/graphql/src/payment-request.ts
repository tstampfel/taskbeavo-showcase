import type { RequestPaymentInput } from './generated/graphql';

/**
 * The helper's Request payment form (BEAV-116, mockup 07), shared by web and mobile. The
 * server checks the same rules (backend/bookings/payment_request.py); checking them here
 * only saves a round trip.
 */

/** The longest note the server accepts. */
export const PAYMENT_REQUEST_NOTE_MAX = 1000;
/** The most photos of the finished job. */
export const PAYMENT_REQUEST_MAX_PHOTOS = 6;

// A JIR is a UUID in its 8-4-4-4-12 form; case doesn't matter.
const JIR = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Whether `value` is in the JIR format. The format only: nobody asks the tax office. */
export function isJir(value: string): boolean {
  return JIR.test(value.trim());
}

export interface PaymentRequestDraft {
  note: string;
  photoIds: readonly string[];
  /** A registered business's invoice: an INVOICE upload, or the JIR typed in. */
  invoiceUploadId: string | null;
  invoiceJir: string;
}

/** What stops the form from being sent; each has a `requestPayment.problems.<key>` text. */
export type PaymentRequestProblem =
  'invoiceMissing' | 'jirInvalid' | 'noteTooLong' | 'tooManyPhotos';

export type PaymentRequestCheck =
  { ok: true; input: RequestPaymentInput } | { ok: false; problem: PaymentRequestProblem };

/**
 * The mutation's input from the form, or the first problem with it. `invoiceRequired` is the
 * booking's: a private helper sends no invoice, whatever the form holds.
 */
export function paymentRequestInput(
  bookingId: string,
  draft: PaymentRequestDraft,
  invoiceRequired: boolean,
): PaymentRequestCheck {
  const note = draft.note.trim();
  const jir = draft.invoiceJir.trim();
  if (note.length > PAYMENT_REQUEST_NOTE_MAX) return { ok: false, problem: 'noteTooLong' };
  if (draft.photoIds.length > PAYMENT_REQUEST_MAX_PHOTOS) {
    return { ok: false, problem: 'tooManyPhotos' };
  }
  const input: RequestPaymentInput = {
    bookingId,
    note: note || null,
    photoIds: draft.photoIds.length > 0 ? [...draft.photoIds] : null,
  };
  if (!invoiceRequired) return { ok: true, input };
  if (draft.invoiceUploadId)
    return { ok: true, input: { ...input, invoiceUploadId: draft.invoiceUploadId } };
  if (!jir) return { ok: false, problem: 'invoiceMissing' };
  if (!isJir(jir)) return { ok: false, problem: 'jirInvalid' };
  return { ok: true, input: { ...input, invoiceJir: jir } };
}

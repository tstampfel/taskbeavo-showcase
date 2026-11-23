'use client';

import { useMutation, useQuery } from '@apollo/client/react';
import {
  BookingDetailDocument,
  RequestPaymentDocument,
  apiErrorCode,
  paymentRequestInput,
  type PaymentRequestProblem,
} from '@taskbeavo/graphql';
import { formatMoney, type Locale } from '@taskbeavo/i18n';
import { useTranslations } from 'next-intl';
import { useId, useState } from 'react';
import { Link } from '../../i18n/navigation';
import { PhotoUploader } from '../../uploads/photo-uploader';
import { Button, Card, ClockIcon, InfoIcon, Input, Note } from '../ui';
import styles from './release.module.css';

/**
 * The helper's Request payment on the web (BEAV-118, mockup 7a): the task and its agreed
 * price, a note, and for a registered business its fiscalized invoice (a photo or scan, or
 * its JIR). Photos of the finished job are evidence, which the API takes only from the app
 * (EVIDENCE_NEEDS_APP), so the page says so. Sending moves no money: the poster releases.
 */
export function BookingRequestPayment({
  bookingId,
  locale,
}: {
  bookingId: string;
  locale: Locale;
}) {
  const t = useTranslations();
  const noteId = useId();
  const booking = useQuery(BookingDetailDocument, {
    variables: { id: bookingId },
    fetchPolicy: 'cache-and-network',
  });
  const [requestPayment, sending] = useMutation(RequestPaymentDocument);
  const [note, setNote] = useState('');
  const [jir, setJir] = useState('');
  const [invoiceUploadId, setInvoiceUploadId] = useState<string | null>(null);
  const [problem, setProblem] = useState<PaymentRequestProblem | null>(null);
  const current = booking.data?.booking;

  if (!current) {
    if (booking.loading) return null;
    return (
      <div className={styles.section}>
        <p className={`${styles.body} ${styles.error}`} role="alert">
          {t('requestPayment.loadError')}
        </p>
        <Button variant="secondary" onClick={() => void booking.refetch().catch(() => undefined)}>
          {t('requestPayment.retry')}
        </Button>
      </div>
    );
  }
  const back = (
    <Link href={`/bookings/${bookingId}`} className={styles.back} data-testid="request-back">
      {t('requestPayment.backToBooking')}
    </Link>
  );
  if (current.status === 'PAYMENT_REQUESTED') {
    return (
      <>
        <Card className={styles.section} data-testid="payment-requested">
          <ClockIcon size={44} />
          <h2 className={styles.h2}>{t('requestPayment.requested')}</h2>
          <p className={styles.body}>
            {t('requestPayment.requestedBody', {
              amount: formatMoney(current.amountCents, locale),
            })}
          </p>
        </Card>
        {back}
      </>
    );
  }
  if (!current.canRequestPayment) {
    return (
      <>
        <Note icon={<InfoIcon size={18} />} data-testid="request-payment-unavailable">
          {t('requestPayment.unavailable')}
        </Note>
        {back}
      </>
    );
  }

  const code = sending.error ? apiErrorCode(sending.error) : undefined;
  const send = () => {
    const check = paymentRequestInput(
      bookingId,
      { note, photoIds: [], invoiceUploadId, invoiceJir: jir },
      current.invoiceRequired,
    );
    if (!check.ok) {
      setProblem(check.problem);
      return;
    }
    setProblem(null);
    void requestPayment({ variables: { input: check.input } }).catch(() => undefined);
  };
  const noteProblem = problem === 'noteTooLong' ? t('requestPayment.problems.noteTooLong') : null;

  return (
    <form
      className={styles.form}
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        send();
      }}
      data-testid="request-payment-form"
    >
      <Card className={styles.section} data-testid="request-payment-summary">
        <h2 className={styles.h2}>{current.task.title}</h2>
        <dl className={styles.rows}>
          <dt className={styles.total}>{t('requestPayment.agreedPrice')}</dt>
          <dd className={styles.total} data-testid="request-payment-price">
            {formatMoney(current.amountCents, locale)}
          </dd>
        </dl>
      </Card>

      {current.invoiceRequired && (
        <Card className={styles.section} data-testid="request-payment-invoice">
          <h3 className={styles.h2}>{t('requestPayment.invoiceTitle')}</h3>
          <p className={styles.body}>{t('requestPayment.invoiceBody')}</p>
          {invoiceUploadId ? (
            <p className={styles.body} data-testid="invoice-added">
              {t('requestPayment.invoiceAdded')}
            </p>
          ) : (
            <>
              <PhotoUploader
                purpose="INVOICE"
                onUploaded={(upload) => setInvoiceUploadId(upload.id)}
              />
              <Input
                label={t('requestPayment.jirLabel')}
                hint={t('requestPayment.jirHint')}
                value={jir}
                onChange={(event) => setJir(event.target.value)}
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                error={
                  problem === 'jirInvalid' || problem === 'invoiceMissing'
                    ? t(`requestPayment.problems.${problem}`)
                    : undefined
                }
                data-testid="request-payment-jir"
              />
            </>
          )}
        </Card>
      )}

      <Note icon={<InfoIcon size={18} />} data-testid="request-payment-photos-in-app">
        {t('requestPayment.photosInApp')}
      </Note>

      <div className={styles.field}>
        <label htmlFor={noteId} className={styles.fieldLabel}>
          {t('requestPayment.noteLabel')}
        </label>
        <textarea
          id={noteId}
          className={styles.textarea}
          value={note}
          onChange={(event) => setNote(event.target.value)}
          aria-invalid={noteProblem ? true : undefined}
          aria-describedby={`${noteId}-hint${noteProblem ? ` ${noteId}-error` : ''}`}
          data-testid="request-payment-note"
        />
        <p id={`${noteId}-hint`} className={`${styles.small} ${styles.muted}`}>
          {t('requestPayment.noteHint')}
        </p>
        {noteProblem && (
          <p id={`${noteId}-error`} role="alert" className={`${styles.small} ${styles.error}`}>
            {noteProblem}
          </p>
        )}
      </div>

      <p className={`${styles.small} ${styles.muted}`}>{t('requestPayment.releaseNote')}</p>
      {code && (
        <p
          className={`${styles.body} ${styles.error}`}
          role="alert"
          data-testid="request-payment-error"
        >
          {t.has(`errors.${code}`) ? t(`errors.${code}`) : t('requestPayment.loadError')}
        </p>
      )}
      <Button
        type="submit"
        fullWidth
        disabled={sending.loading}
        aria-busy={sending.loading}
        data-testid="request-payment-submit"
      >
        {t('requestPayment.submit')}
      </Button>
    </form>
  );
}

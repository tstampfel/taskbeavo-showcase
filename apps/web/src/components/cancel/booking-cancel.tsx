'use client';

import { useMutation, useQuery } from '@apollo/client/react';
import { legalPath } from '@taskbeavo/app-links';
import {
  BookingCancellationDocument,
  CancelAsHelperDocument,
  CancelBookingDocument,
  HELPER_CANCEL_REASONS,
  NO_FAULT_REASONS,
  POSTER_CANCEL_REASONS,
  ReopenTaskDocument,
  RequestNoFaultCancellationDocument,
  RespondToNoFaultCancellationDocument,
  ViewerDocument,
  WithdrawFromContractDocument,
  apiErrorCode,
  cancelRole,
  cancelView,
  hoursLeft,
  noFaultDecision,
  type CancelPreview,
  type CancelRole,
  type HelperCancelReason,
  type NoFaultPending,
  type PickedReason,
  type PosterCancelReason,
  type WithdrawalOffer,
} from '@taskbeavo/graphql';
import { formatDate, formatMoney, type Locale } from '@taskbeavo/i18n';
import { useTranslations } from 'next-intl';
import { useState } from 'react';
import { Link } from '../../i18n/navigation';
import { Button, Card, Input } from '../ui';
import styles from './cancel.module.css';

/**
 * The cancel screen for both sides of a booking (mockup 13). The poster sees the refund
 * preview and the withdrawal button; the helper sees what their cancel means for the poster
 * (13g). Either side may cite a no-fault reason, which the other side answers (13f). Every
 * amount is the server's (bookings/cancellation.py); the mutations work it out again. The
 * withdrawal button stays while the ordinary cancel is refused with WORK_STARTED.
 */
export function BookingCancel({ bookingId, locale }: { bookingId: string; locale: Locale }) {
  const t = useTranslations('cancel');
  const tErrors = useTranslations('errors');
  const booking = useQuery(BookingCancellationDocument, {
    variables: { id: bookingId },
    fetchPolicy: 'cache-and-network',
  });
  const [reason, setReason] = useState<PickedReason | null>(null);
  const [cancel, cancelling] = useMutation(CancelBookingDocument);
  const [cancelAsHelper, helperCancelling] = useMutation(CancelAsHelperDocument);
  const [requestNoFault, requesting] = useMutation(RequestNoFaultCancellationDocument);
  const [respondNoFault, responding] = useMutation(RespondToNoFaultCancellationDocument);
  const [reopen, reopening] = useMutation(ReopenTaskDocument);
  const [withdraw, withdrawing] = useMutation(WithdrawFromContractDocument);
  // Tells the sides apart, and prefills the withdrawal statement (ZZP čl. 81.a(3)).
  const viewerQuery = useQuery(ViewerDocument);
  const viewer = viewerQuery.data?.viewer;
  const current = booking.data?.booking;

  if (!current || (!viewer && viewerQuery.loading)) {
    if (booking.loading || viewerQuery.loading) return null;
    return (
      <div className={styles.section}>
        <p className={`${styles.body} ${styles.error}`} role="alert">
          {t('loadError')}
        </p>
        <Button variant="secondary" onClick={() => void booking.refetch().catch(() => undefined)}>
          {t('retry')}
        </Button>
      </div>
    );
  }

  const role = cancelRole(current, viewer?.id ?? null);
  const view = cancelView(current, role);
  const decision = noFaultDecision(current);
  const mutations = [cancelling, helperCancelling, requesting, responding, reopening, withdrawing];
  const error = mutations.find((m) => m.error)?.error;
  const errorCode = error ? apiErrorCode(error) : undefined;
  const money = (cents: number) => formatMoney(cents, locale);
  const busy = mutations.some((m) => m.loading);
  const onWithdraw = (name: string, email: string) =>
    void withdraw({ variables: { id: bookingId, name, email } }).catch(() => undefined);
  const refresh = () => void booking.refetch().catch(() => undefined);
  const onNoFault = (key: PickedReason & { kind: 'noFault' }) =>
    void requestNoFault({ variables: { id: bookingId, reason: key.key } }).catch(refresh);

  if (view.kind === 'unknownViewer') {
    // Never show one side's screen to someone whose side isn't known (R1-11).
    return (
      <div className={styles.section}>
        <p className={`${styles.body} ${styles.error}`} role="alert">
          {t('loadError')}
        </p>
        <Button
          variant="secondary"
          onClick={() => void viewerQuery.refetch().catch(() => undefined)}
        >
          {t('retry')}
        </Button>
      </div>
    );
  }

  return (
    <>
      {decision && (
        <p className={styles.body} role="status" data-testid="no-fault-decision">
          {t(`decision.${decision}`)}
        </p>
      )}

      {view.kind === 'done' && (
        <Card className={styles.section}>
          <h2 className={styles.h2}>
            {view.withdrawal
              ? t('done.withdrawn')
              : view.byHelper && role === 'poster'
                ? t('done.byHelper')
                : t('done.cancelled')}
          </h2>
          {view.awaitingSupport ? (
            <p className={styles.body}>{t('done.awaitingSupport')}</p>
          ) : (
            view.refundCents !== null && (
              <p className={styles.body}>
                {view.refunding
                  ? t('done.refunding', { amount: money(view.refundCents) })
                  : t('done.refunded', { amount: money(view.refundCents) })}
              </p>
            )
          )}
          {view.withdrawal && (
            <p className={`${styles.small} ${styles.muted}`}>{t('done.emailed')}</p>
          )}
          {role === 'poster' && view.creditBackCents > 0 && (
            <p className={styles.body} data-testid="cancel-credit-back">
              {t('done.creditBack', { amount: money(view.creditBackCents) })}
            </p>
          )}
          {role === 'poster' && view.posterCreditCents > 0 && (
            <p className={styles.body} data-testid="cancel-credit">
              {t('done.credit', { amount: money(view.posterCreditCents) })}
            </p>
          )}
          {view.reopened && role === 'poster' && (
            <p className={styles.body}>{t('done.reopened')}</p>
          )}
          {view.canReopen && (
            <>
              <p className={`${styles.small} ${styles.muted}`}>{t('done.reopenBody')}</p>
              <Button
                variant="secondary"
                fullWidth
                disabled={busy}
                onClick={() => void reopen({ variables: { bookingId } }).catch(refresh)}
                data-testid="cancel-reopen"
              >
                {reopening.loading ? t('done.reopening') : t('done.reopen')}
              </Button>
            </>
          )}
        </Card>
      )}

      {view.kind === 'noFaultPending' && (
        <Pending
          pending={view}
          role={role}
          busy={busy}
          onRespond={(agree) =>
            void respondNoFault({ variables: { id: bookingId, agree } }).catch(refresh)
          }
        />
      )}

      {view.kind === 'helperCancel' && (
        <>
          <Card className={styles.section}>
            <h2 className={styles.h2}>{t('helper.title')}</h2>
            <p className={styles.body} data-testid="helper-cancel-warning">
              {view.posterCreditCents > 0
                ? t('helper.warningCredit', {
                    refund: money(view.refundCents),
                    credit: money(view.posterCreditCents),
                  })
                : t('helper.warning', { refund: money(view.refundCents) })}
            </p>
            <p className={`${styles.small} ${styles.muted}`}>{t('helper.neverPay')}</p>
          </Card>
          <Reasons
            legend={t('helper.reasonsLegend')}
            ordinary={HELPER_CANCEL_REASONS}
            ordinaryLabel={(key) => t(`helper.reasons.${key}`)}
            noFault={view.noFault}
            picked={reason}
            onPick={setReason}
          />
          <p className={`${styles.small} ${styles.muted}`}>{t('helper.tip')}</p>
          {reason?.kind === 'noFault' ? (
            <NoFaultSend role={role} busy={busy} onSend={() => onNoFault(reason)} />
          ) : (
            <Button
              variant="danger"
              fullWidth
              disabled={reason === null || busy}
              onClick={() => {
                if (reason?.kind !== 'ordinary') return;
                const key = reason.key as HelperCancelReason;
                void cancelAsHelper({ variables: { id: bookingId, reason: key } }).catch(refresh);
              }}
              data-testid="helper-cancel-confirm"
            >
              {helperCancelling.loading ? t('helper.cancelling') : t('helper.confirm')}
            </Button>
          )}
        </>
      )}

      {view.kind === 'cancel' && (
        <>
          <Reasons
            legend={t('reasonsLegend')}
            ordinary={POSTER_CANCEL_REASONS}
            ordinaryLabel={(key) => t(`reasons.${key}`)}
            noFault={view.noFault}
            picked={reason}
            onPick={setReason}
          />
          {view.noFault && <p className={`${styles.small} ${styles.muted}`}>{t('noFault')}</p>}
          {reason?.kind === 'noFault' ? (
            <NoFaultSend role={role} busy={busy} onSend={() => onNoFault(reason)} />
          ) : (
            <>
              <Preview preview={view.preview} money={money} />
              <Button
                variant="danger"
                fullWidth
                disabled={reason === null || busy}
                onClick={() => {
                  if (reason?.kind !== 'ordinary') return;
                  const key = reason.key as PosterCancelReason;
                  const expectedRefundCents = view.preview.refundCents;
                  void cancel({
                    variables: { id: bookingId, reason: key, expectedRefundCents },
                  }).catch((e: unknown) => {
                    // A deadline passed while the screen was open: show the new preview.
                    if (apiErrorCode(e) === 'CANCELLATION_CHANGED') refresh();
                  });
                }}
              >
                {cancelling.loading ? t('cancelling') : t('confirm')}
              </Button>
            </>
          )}
        </>
      )}

      {(view.kind === 'refused' || view.kind === 'withdrawOnly') && (
        <p className={styles.body}>
          {role === 'helper'
            ? t('helper.refused')
            : view.refusedWith === 'WORK_STARTED'
              ? t('refused.WORK_STARTED')
              : t('refused.BOOKING_ACTION_NOT_ALLOWED')}
        </p>
      )}

      {view.kind === 'unavailable' && <p className={styles.body}>{t('unavailable')}</p>}

      {(view.kind === 'cancel' || view.kind === 'withdrawOnly') && view.withdrawal && (
        <Withdrawal
          offer={view.withdrawal}
          started={view.kind === 'withdrawOnly'}
          bookingId={bookingId}
          defaultName={viewer ? `${viewer.firstName} ${viewer.lastName}`.trim() : ''}
          defaultEmail={viewer?.email ?? ''}
          locale={locale}
          busy={busy}
          sending={withdrawing.loading}
          onWithdraw={onWithdraw}
        />
      )}

      {errorCode && (
        <p className={`${styles.body} ${styles.error}`} role="alert">
          {tErrors.has(errorCode) ? tErrors(errorCode) : t('loadError')}
        </p>
      )}

      <a className={styles.link} href={legalPath('cancellation', locale)}>
        {t('policyLink')}
      </a>
      <Link className={styles.link} href={`/bookings/${bookingId}`}>
        {t('back')}
      </Link>
    </>
  );
}

/** The ordinary reasons and, when allowed, the no-fault ones: one radio group (13c, 13g). */
function Reasons<K extends string>({
  legend,
  ordinary,
  ordinaryLabel,
  noFault,
  picked,
  onPick,
}: {
  legend: string;
  ordinary: readonly K[];
  ordinaryLabel(key: K): string;
  noFault: boolean;
  picked: PickedReason | null;
  onPick(reason: PickedReason): void;
}) {
  const t = useTranslations('cancel');
  const radio = (reason: PickedReason, label: string) => (
    <label key={`${reason.kind}-${reason.key}`} className={styles.reason}>
      <input
        type="radio"
        name="reason"
        value={reason.key}
        checked={picked?.kind === reason.kind && picked.key === reason.key}
        onChange={() => onPick(reason)}
        data-testid={`cancel-reason-${reason.key}`}
      />
      {label}
    </label>
  );
  return (
    <>
      <fieldset className={styles.reasons}>
        <legend className={styles.h2}>{legend}</legend>
        {ordinary.map((key) =>
          radio(
            { kind: 'ordinary', key: key as PickedReason['key'] } as PickedReason,
            ordinaryLabel(key),
          ),
        )}
      </fieldset>
      {noFault && (
        <fieldset className={styles.reasons}>
          <legend className={styles.h2}>{t('noFaultLegend')}</legend>
          {NO_FAULT_REASONS.map((key) =>
            radio({ kind: 'noFault', key }, t(`noFaultReasons.${key}`)),
          )}
        </fieldset>
      )}
    </>
  );
}

function NoFaultSend({ role, busy, onSend }: { role: CancelRole; busy: boolean; onSend(): void }) {
  const t = useTranslations('cancel');
  return (
    <>
      <p className={styles.body}>{t(`noFaultRequest.${role}`)}</p>
      <Button
        variant="danger"
        fullWidth
        disabled={busy}
        onClick={onSend}
        data-testid="no-fault-send"
      >
        {busy ? t('noFaultRequest.sending') : t('noFaultRequest.send')}
      </Button>
    </>
  );
}

/** A no-fault request waiting for the other side or for support (13e, 13f). */
function Pending({
  pending,
  role,
  busy,
  onRespond,
}: {
  pending: NoFaultPending;
  role: CancelRole;
  busy: boolean;
  onRespond(agree: boolean): void;
}) {
  const t = useTranslations('cancel');
  // The other side sees only the generic label (GDPR Art. 9, decision 4).
  const reason =
    pending.requestedByYou && pending.reason
      ? t(`noFaultReasons.${pending.reason}`)
      : t(`noFaultLabels.${pending.label}`);
  const asked = pending.requestedByYou
    ? t('pending.youAsked', { reason })
    : role === 'helper'
      ? t('pending.posterAsked', { reason })
      : t('pending.helperAsked', { reason });
  return (
    <Card className={styles.section} data-testid="no-fault-pending">
      <h2 className={styles.h2}>
        {pending.awaitingSupport
          ? t('pending.underReview')
          : t('pending.hoursLeft', { hours: hoursLeft(pending.respondBy) })}
      </h2>
      <p className={styles.body}>{asked}</p>
      {pending.awaitingSupport && <p className={styles.body}>{t('pending.underReviewBody')}</p>}
      {/* The poster may still answer a helper's request until support decides. */}
      {pending.canRespond ? (
        <>
          <p className={styles.body}>
            {role === 'helper'
              ? t('pending.helperAnswer')
              : pending.awaitingSupport
                ? t('pending.posterAnswerUnderReview')
                : t('pending.posterAnswer')}
          </p>
          <Button
            fullWidth
            disabled={busy}
            onClick={() => onRespond(true)}
            data-testid="no-fault-agree"
          >
            {t('pending.agree')}
          </Button>
          <Button
            variant="secondary"
            fullWidth
            disabled={busy}
            onClick={() => onRespond(false)}
            data-testid="no-fault-disagree"
          >
            {t('pending.disagree')}
          </Button>
        </>
      ) : (
        !pending.awaitingSupport && (
          <p className={`${styles.small} ${styles.muted}`}>{t('pending.waiting')}</p>
        )
      )}
    </Card>
  );
}

function Preview({ preview, money }: { preview: CancelPreview; money: (cents: number) => string }) {
  const t = useTranslations('cancel');
  return (
    <Card className={styles.section}>
      <h2 className={styles.h2}>{preview.full ? t('preview.full') : t('preview.title')}</h2>
      <p className={styles.body}>{t(`cases.${preview.case}`)}</p>
      <dl className={styles.rows}>
        <dt className={styles.strong}>{t('preview.youGetBack')}</dt>
        <dd className={styles.strong}>{money(preview.refundCents)}</dd>
        {preview.helperCompensationCents > 0 && (
          <>
            <dt>{t('preview.helperKeeps')}</dt>
            <dd>{money(preview.helperCompensationCents)}</dd>
            <dt>{t('preview.keptFee')}</dt>
            <dd>{money(preview.keptFeeCents)}</dd>
          </>
        )}
        <dt className={styles.muted}>{t('preview.youPaid')}</dt>
        <dd className={styles.muted}>{money(preview.chargeCents)}</dd>
      </dl>
      {preview.creditBackCents > 0 && (
        <p className={styles.body} data-testid="cancel-preview-credit-back">
          {t('preview.creditBack', { amount: money(preview.creditBackCents) })}
        </p>
      )}
      <p className={`${styles.small} ${styles.muted}`}>{t('preview.toCard')}</p>
    </Card>
  );
}

function Withdrawal({
  offer,
  started,
  bookingId,
  defaultName,
  defaultEmail,
  locale,
  busy,
  sending,
  onWithdraw,
}: {
  offer: WithdrawalOffer;
  started: boolean;
  bookingId: string;
  defaultName: string;
  defaultEmail: string;
  locale: Locale;
  busy: boolean;
  sending: boolean;
  onWithdraw: (name: string, email: string) => void;
}) {
  const t = useTranslations('cancel');
  // The withdrawal is final: a second, explicit confirmation first, with the statement's
  // name, booking and confirmation email prefilled and editable (ZZP čl. 81.a(3)).
  const [confirming, setConfirming] = useState(false);
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const start = () => {
    setName(defaultName);
    setEmail(defaultEmail);
    setConfirming(true);
  };
  return (
    <Card className={styles.section}>
      <h2 className={styles.h2}>{t('withdrawal.title')}</h2>
      {offer.endsAt && (
        <p className={styles.small}>
          {t('withdrawal.until', { date: lastDay(offer.endsAt, locale) })}
        </p>
      )}
      <p className={styles.body}>
        {started ? t('withdrawal.afterStart') : t('withdrawal.beforeStart')}
      </p>
      {confirming ? (
        <div className={styles.section} role="group" aria-labelledby="withdraw-confirm-title">
          <p id="withdraw-confirm-title" className={`${styles.body} ${styles.strong}`}>
            {t('withdrawal.confirmTitle')}
          </p>
          <p className={styles.body}>{t('withdrawal.confirmBody')}</p>
          <p className={styles.small}>
            {t('withdrawal.contract')}: {bookingId}
          </p>
          <Input
            label={t('withdrawal.name')}
            value={name}
            autoComplete="name"
            onChange={(event) => setName(event.target.value)}
          />
          <Input
            label={t('withdrawal.email')}
            hint={t('withdrawal.formIntro')}
            type="email"
            value={email}
            autoComplete="email"
            onChange={(event) => setEmail(event.target.value)}
          />
          <Button
            variant="danger"
            fullWidth
            disabled={busy || !name.trim() || !email.trim()}
            onClick={() => onWithdraw(name, email)}
          >
            {sending ? t('withdrawal.sending') : t('withdrawal.confirm')}
          </Button>
          <Button variant="ghost" fullWidth disabled={busy} onClick={() => setConfirming(false)}>
            {t('withdrawal.keep')}
          </Button>
        </div>
      ) : (
        <Button variant="secondary" fullWidth disabled={busy} onClick={start}>
          {t('withdrawal.button')}
        </Button>
      )}
    </Card>
  );
}

/** The period ends at 24:00 Zagreb time: the last day is the one before that midnight. */
function lastDay(endsAt: string, locale: Locale): string {
  return formatDate(new Date(new Date(endsAt).getTime() - 1), locale, 'long');
}

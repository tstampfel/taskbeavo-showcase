'use client';

import { useApolloClient, useMutation, useQuery } from '@apollo/client/react';
import {
  AddTipDocument,
  BookingDetailDocument,
  PartialReleasePreviewDocument,
  ProposePartialReleaseDocument,
  ReleasePaymentDocument,
  RespondToPartialReleaseDocument,
  ViewerDocument,
  WithdrawPartialReleaseDocument,
  apiErrorCode,
  bookingRole,
  partialReleaseCheck,
  releaseSummary,
  releaseView,
  subscribeToBookingDetail,
  type PartialReleasePreviewQuery,
  type PaymentRequestFieldsFragment,
  type TipState,
} from '@taskbeavo/graphql';
import { formatDateTime, formatMoney, type Locale } from '@taskbeavo/i18n';
import dynamic from 'next/dynamic';
import { useTranslations } from 'next-intl';
import { useEffect, useState, type ReactNode } from 'react';
import { Link } from '../../i18n/navigation';
import { stripeReturn } from '../checkout/stripe-return';
import { Button, Card, ClockIcon, InfoIcon, Input, Note, Sheet } from '../ui';
import styles from './release.module.css';

// Stripe.js loads only when the poster pays a tip (BEAV-190, src/stripe-imports.test.ts).
const TipCardForm = dynamic(() => import('./tip-payment'), { ssr: false });

type Translate = ReturnType<typeof useTranslations>;

/** A failed mutation's text: the release page's own first, then the shared error, else generic. */
export function releaseErrorKey(t: Translate, error: unknown): string | null {
  if (!error) return null;
  const code = apiErrorCode(error);
  if (code && t.has(`release.errors.${code}`)) return `release.errors.${code}`;
  if (code && t.has(`errors.${code}`)) return `errors.${code}`;
  return 'release.loadError';
}

/**
 * Release payment (BEAV-118, mockups 7d–7f) for both sides, from the server's booking. The
 * poster sees the helper's photos, note and invoice, picks a tip (none preselected), confirms
 * (7e) and releases; a tip is then paid with Stripe's Payment Element, loaded only then, and
 * counts once Stripe's webhook confirms it (hard rule a). The poster may instead propose a
 * lower amount after the server's preview; the helper answers it here. Live through
 * bookingUpdated, refetched when the tab becomes visible again.
 */
export function BookingRelease({ bookingId, locale }: { bookingId: string; locale: Locale }) {
  const t = useTranslations();
  const money = (cents: number) => formatMoney(cents, locale);
  const booking = useQuery(BookingDetailDocument, {
    variables: { id: bookingId },
    fetchPolicy: 'cache-and-network',
  });
  const viewerQuery = useQuery(ViewerDocument);
  const { subscribeToMore, refetch } = booking;
  useEffect(
    () => subscribeToBookingDetail(subscribeToMore, bookingId),
    [subscribeToMore, bookingId],
  );
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') void refetch().catch(() => undefined);
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [refetch]);
  const variables = { bookingId };
  const [release, releasing] = useMutation(ReleasePaymentDocument);
  const [propose, proposing] = useMutation(ProposePartialReleaseDocument);
  const [withdraw, withdrawing] = useMutation(WithdrawPartialReleaseDocument);
  const [respond, responding] = useMutation(RespondToPartialReleaseDocument);
  const [addTip] = useMutation(AddTipDocument, { fetchPolicy: 'no-cache' });
  const [tip, setTip] = useState<number | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [partialOpen, setPartialOpen] = useState(false);
  const [tipPayment, setTipPayment] = useState<TipPaymentState>({ kind: 'idle' });

  useEffect(() => {
    // Back from the bank's page after paying a tip (3-D Secure), as on checkout: Stripe has
    // the tip when it says succeeded or processing; the webhook still decides (hard rule a).
    // The client secret leaves the address bar and history (review R1-2).
    const back = stripeReturn(new URL(window.location.href));
    if (!back) return;
    setTipPayment(
      back.outcome === 'submitted'
        ? { kind: 'confirming' }
        : { kind: 'failed', key: 'release.tip.failed' },
    );
    window.history.replaceState(null, '', back.cleanUrl);
  }, []);

  const current = booking.data?.booking;
  const viewer = viewerQuery.data?.viewer;

  const retry = (
    <div className={styles.section}>
      <p className={`${styles.body} ${styles.error}`} role="alert">
        {t('release.loadError')}
      </p>
      <Button
        variant="secondary"
        onClick={() => {
          void booking.refetch().catch(() => undefined);
          void viewerQuery.refetch().catch(() => undefined);
        }}
      >
        {t('release.retry')}
      </Button>
    </div>
  );
  if (!current || (!viewer && viewerQuery.loading)) {
    return booking.loading || viewerQuery.loading ? null : retry;
  }
  const view = releaseView(current, bookingRole(current, viewer?.id ?? null));

  const startTip = (amountCents: number) => {
    setTipPayment({ kind: 'starting', amountCents });
    void addTip({ variables: { bookingId, amountCents } })
      .then(({ data }) => {
        const secret = data?.addTip.clientSecret;
        if (!secret) throw new Error('no client secret');
        setTipPayment({ kind: 'form', amountCents, clientSecret: secret });
      })
      .catch((error: unknown) =>
        setTipPayment({
          kind: 'failed',
          key: releaseErrorKey(t, error) ?? 'release.tip.failed',
        }),
      );
  };

  const actionKey =
    releaseErrorKey(t, releasing.error) ??
    releaseErrorKey(t, proposing.error) ??
    releaseErrorKey(t, withdrawing.error) ??
    releaseErrorKey(t, responding.error);
  const errorLine = actionKey ? (
    <p className={`${styles.body} ${styles.error}`} role="alert" data-testid="release-error">
      {t(actionKey)}
    </p>
  ) : null;
  const back = (
    <Link href={`/bookings/${bookingId}`} className={styles.back} data-testid="release-back">
      {t('release.released.backToBooking')}
    </Link>
  );

  const doRelease = () => {
    const chosen = tip;
    void release({ variables })
      .then(() => {
        setConfirming(false);
        if (chosen) startTip(chosen);
      })
      .catch(() => setConfirming(false));
  };
  const confirmSheet = (
    <ConfirmSheet
      open={confirming}
      priceCents={current.amountCents}
      tipCents={tip}
      busy={releasing.loading}
      money={money}
      onRelease={doRelease}
      onClose={() => setConfirming(false)}
    />
  );

  switch (view.kind) {
    case 'unknownViewer':
      return retry;

    case 'release':
      return (
        <>
          <RequestSummary
            request={current.paymentRequest}
            title={current.task.title}
            time={(at) => (typeof at === 'string' ? formatDateTime(at, locale, 'short') : '')}
          />
          <dl className={styles.rows}>
            <dt className={styles.total}>{t('release.amount')}</dt>
            <dd className={styles.total} data-testid="release-amount">
              {money(view.priceCents)}
            </dd>
          </dl>
          {view.declined && (
            <Note tone="warn" icon={<InfoIcon size={18} />} data-testid="release-declined">
              {t('release.declined', { amount: money(view.declined.releasedCents) })}
            </Note>
          )}
          {view.tipChoices.length > 0 && (
            <>
              <TipChips
                legend={t('release.tipTitle')}
                choices={view.tipChoices}
                picked={tip}
                onPick={setTip}
                money={money}
              />
              <p className={`${styles.small} ${styles.muted}`}>{t('release.tipNote')}</p>
            </>
          )}
          {current.task.onSiteContact && (
            <Note icon={<InfoIcon size={18} />}>{t('release.checkFirst')}</Note>
          )}
          {errorLine}
          <div className={styles.actions}>
            <Button fullWidth onClick={() => setConfirming(true)} data-testid="release-submit">
              {t('release.submit')}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setPartialOpen(true)}
              data-testid="release-lower"
            >
              {t('release.lower')}
            </Button>
          </div>
          {confirmSheet}
          <PartialSheet
            open={partialOpen}
            bookingId={bookingId}
            priceCents={view.priceCents}
            busy={proposing.loading}
            money={money}
            onClose={() => setPartialOpen(false)}
            onSend={(releasedCents) =>
              void propose({ variables: { bookingId, releasedCents } })
                .then(() => setPartialOpen(false))
                .catch(() => setPartialOpen(false))
            }
          />
        </>
      );

    case 'proposalOpen':
      return (
        <>
          <Card className={styles.section} data-testid="release-proposal-open">
            <h2 className={styles.h2}>{t('release.proposalOpen.title')}</h2>
            <p className={styles.body}>
              {t('release.proposalOpen.body', {
                released: money(view.releasedCents),
                price: money(view.priceCents),
              })}
            </p>
            {view.refundCents != null && (
              <p className={styles.body}>
                {t('release.proposalOpen.refund', { refund: money(view.refundCents) })}
              </p>
            )}
          </Card>
          {errorLine}
          <div className={styles.actions}>
            <Button
              variant="secondary"
              fullWidth
              disabled={withdrawing.loading}
              aria-busy={withdrawing.loading}
              onClick={() => void withdraw({ variables }).catch(() => undefined)}
              data-testid="release-withdraw"
            >
              {t('release.proposalOpen.withdraw')}
            </Button>
            {view.canRelease && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setTip(null);
                  setConfirming(true);
                }}
                data-testid="release-full"
              >
                {t('release.proposalOpen.releaseFull', { price: money(view.priceCents) })}
              </Button>
            )}
          </div>
          {confirmSheet}
        </>
      );

    case 'partialSettled': {
      const helper = current.helperUserId === viewer?.id;
      return (
        <>
          <Card className={styles.section} data-testid="release-partial-settled">
            <h2 className={styles.h2}>{t('release.partialSettled.title')}</h2>
            <p className={styles.body}>
              {helper
                ? t('release.partialSettled.helper', { released: money(view.releasedCents) })
                : t('release.partialSettled.body', { released: money(view.releasedCents) })}
            </p>
            {!helper && view.refundCents != null && (
              <p className={styles.body}>
                {t(
                  view.done
                    ? 'release.partialSettled.refundDone'
                    : 'release.partialSettled.refundPending',
                  { refund: money(view.refundCents) },
                )}
              </p>
            )}
          </Card>
          {back}
        </>
      );
    }

    case 'released':
      return (
        <>
          <Card className={styles.section} data-testid="release-released">
            <h2 className={styles.h1}>{t('release.released.title')}</h2>
            <p className={styles.body}>
              {t('release.released.body', { amount: money(view.priceCents) })}
            </p>
            <dl className={styles.rows}>
              <dt>{t('release.released.payout')}</dt>
              <dd className={styles.total}>{t('release.released.payoutTime')}</dd>
            </dl>
          </Card>
          <TipSection
            state={view.tip}
            picked={tip}
            onPick={setTip}
            payment={tipPayment}
            money={money}
            onPay={startTip}
            form={(amountCents, clientSecret) => (
              <TipCardForm
                clientSecret={clientSecret}
                bookingId={bookingId}
                amountCents={amountCents}
                locale={locale}
                onPaid={() => {
                  setTipPayment({ kind: 'confirming' });
                  void booking.refetch().catch(() => undefined);
                }}
              />
            )}
          />
          {back}
        </>
      );

    case 'answer':
      return (
        <>
          <Card className={styles.section} data-testid="release-answer">
            <h2 className={styles.h2}>{t('release.answer.title')}</h2>
            <p className={styles.body}>
              {t('release.answer.body', {
                released: money(view.releasedCents),
                price: money(view.priceCents),
              })}
            </p>
          </Card>
          {errorLine}
          <div className={styles.actions}>
            <Button
              fullWidth
              disabled={responding.loading}
              aria-busy={responding.loading}
              onClick={() =>
                void respond({ variables: { bookingId, accept: true } }).catch(() => undefined)
              }
              data-testid="release-accept"
            >
              {t('release.answer.accept', { released: money(view.releasedCents) })}
            </Button>
            <Button
              variant="secondary"
              fullWidth
              disabled={responding.loading}
              onClick={() =>
                void respond({ variables: { bookingId, accept: false } }).catch(() => undefined)
              }
              data-testid="release-decline"
            >
              {t('release.answer.decline')}
            </Button>
          </div>
        </>
      );

    case 'helperWaiting':
      return (
        <>
          <Note icon={<ClockIcon size={18} />} data-testid="release-helper-waiting">
            {t('release.helperWaiting', { amount: money(view.priceCents) })}
          </Note>
          {back}
        </>
      );

    case 'helperReleased':
      return (
        <>
          <Card className={styles.section} data-testid="release-helper-released">
            <p className={styles.body}>
              {t('release.helperReleased', { amount: money(view.priceCents) })}
            </p>
            {view.tipCents != null && (
              <p className={styles.body}>
                {t('release.helperTip', { amount: money(view.tipCents) })}
              </p>
            )}
          </Card>
          {back}
        </>
      );

    case 'notRequested':
      return (
        <>
          <Note icon={<InfoIcon size={18} />} data-testid="release-not-requested">
            {t('release.notRequested')}
          </Note>
          {back}
        </>
      );

    case 'unavailable':
      return (
        <>
          <Note icon={<InfoIcon size={18} />} data-testid="release-unavailable">
            {t('release.unavailable')}
          </Note>
          {back}
        </>
      );
  }
}

/** Mockup 7d's top: the helper's photos, note and invoice. Photo URLs are short-lived. */
function RequestSummary({
  request,
  title,
  time,
}: {
  request: PaymentRequestFieldsFragment | null;
  title: string;
  time(at: unknown): string;
}) {
  const t = useTranslations('release');
  const requested = request ? t('requestedAt', { time: time(request.requestedAt) }) : null;
  return (
    <>
      <div className={styles.section}>
        <h2 className={styles.h2}>{t('finished')}</h2>
        <p className={`${styles.small} ${styles.muted}`}>
          {requested ? `${title} · ${requested}` : title}
        </p>
      </div>
      {request && request.photos.some((photo) => photo.url) && (
        <ul className={styles.photos} aria-label={t('photosTitle')} data-testid="release-photos">
          {request.photos.map((photo, index) =>
            photo.url ? (
              <li key={photo.id}>
                {/* Short-lived R2 links without metadata; next/image can't cache them. */}
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  className={styles.photo}
                  src={photo.url}
                  alt={t('photoLabel', { number: index + 1 })}
                />
              </li>
            ) : null,
          )}
        </ul>
      )}
      {request?.note ? (
        <Card className={styles.section} data-testid="release-note">
          <p className={styles.caption}>{t('noteTitle')}</p>
          <p className={styles.body}>{request.note}</p>
        </Card>
      ) : null}
      {request && (request.invoiceJir || request.invoice) && (
        <Card className={styles.section} data-testid="release-invoice">
          <p className={styles.caption}>{t('invoiceTitle')}</p>
          {request.invoiceJir && (
            <p className={styles.body}>{t('invoiceJir', { jir: request.invoiceJir })}</p>
          )}
          {request.invoice?.url && (
            // eslint-disable-next-line @next/next/no-img-element
            <img className={styles.invoice} src={request.invoice.url} alt={t('invoicePhoto')} />
          )}
        </Card>
      )}
    </>
  );
}

/** The tip chips of mockup 7d as radio buttons: none preselected, and "No tip". */
function TipChips({
  legend,
  choices,
  picked,
  onPick,
  money,
  disabled = false,
}: {
  legend: string;
  choices: number[];
  picked: number | null;
  onPick(cents: number | null): void;
  money(cents: number): string;
  disabled?: boolean;
}) {
  const t = useTranslations('release');
  const options = [
    ...choices.map((cents) => ({ cents, label: money(cents) })),
    { cents: null, label: t('tipNone') },
  ];
  return (
    <fieldset className={styles.chips} disabled={disabled} data-testid="release-tips">
      <legend>{legend}</legend>
      {options.map(({ cents, label }) => (
        <label key={cents ?? 'none'} className={styles.chip}>
          <input
            type="radio"
            name="tip"
            checked={picked === cents}
            onChange={() => onPick(cents)}
            data-testid={`release-tip-${cents ?? 'none'}`}
          />
          {label}
        </label>
      ))}
    </fieldset>
  );
}

/**
 * Mockup 7e: what the release pays the helper, and that it can't be undone. The title is the
 * task price, since releasePayment pays only that; a tip is its own card payment right after
 * (review R1-3).
 */
function ConfirmSheet({
  open,
  priceCents,
  tipCents,
  busy,
  money,
  onRelease,
  onClose,
}: {
  open: boolean;
  priceCents: number;
  tipCents: number | null;
  busy: boolean;
  money(cents: number): string;
  onRelease(): void;
  onClose(): void;
}) {
  const t = useTranslations('release.confirm');
  const summary = releaseSummary(priceCents, tipCents);
  return (
    <Sheet
      open={open}
      onClose={onClose}
      title={t('title', { amount: money(summary.priceCents) })}
      closeLabel={t('cancel')}
    >
      <div className={styles.section} data-testid="release-confirm">
        <dl className={styles.rows}>
          <dt>{t('price')}</dt>
          <dd>{money(summary.priceCents)}</dd>
          {summary.tipCents > 0 && (
            <>
              <dt>{t('tip')}</dt>
              <dd data-testid="release-confirm-tip">{money(summary.tipCents)}</dd>
            </>
          )}
        </dl>
        {summary.tipCents > 0 && (
          <p className={`${styles.small} ${styles.muted}`} data-testid="release-confirm-tip-note">
            {t('tipByCard')}
          </p>
        )}
        <p className={`${styles.body} ${styles.muted}`}>{t('irreversible')}</p>
        <Button
          fullWidth
          disabled={busy}
          aria-busy={busy}
          onClick={onRelease}
          data-testid="release-confirm-release"
        >
          {t('release')}
        </Button>
        <Button variant="secondary" fullWidth onClick={onClose}>
          {t('cancel')}
        </Button>
      </div>
    </Sheet>
  );
}

/** "Release a lower amount": the amount, then the server's preview, then the proposal. */
function PartialSheet({
  open,
  bookingId,
  priceCents,
  busy,
  money,
  onClose,
  onSend,
}: {
  open: boolean;
  bookingId: string;
  priceCents: number;
  busy: boolean;
  money(cents: number): string;
  onClose(): void;
  onSend(releasedCents: number): void;
}) {
  const t = useTranslations();
  const client = useApolloClient();
  const [text, setText] = useState('');
  const [problem, setProblem] = useState<'format' | 'range' | null>(null);
  const [preview, setPreview] = useState<
    PartialReleasePreviewQuery['partialReleasePreview'] | null
  >(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);

  const close = () => {
    setText('');
    setProblem(null);
    setPreview(null);
    setFailed(null);
    onClose();
  };
  const check = () => {
    const result = partialReleaseCheck(text, priceCents);
    if (!result.ok) {
      setProblem(result.problem);
      return;
    }
    setProblem(null);
    setFailed(null);
    setLoading(true);
    client
      .query({
        query: PartialReleasePreviewDocument,
        variables: { bookingId, releasedCents: result.releasedCents },
        fetchPolicy: 'no-cache',
      })
      .then(({ data }) => setPreview(data?.partialReleasePreview ?? null))
      .catch((error: unknown) => setFailed(releaseErrorKey(t, error)))
      .finally(() => setLoading(false));
  };

  return (
    <Sheet
      open={open}
      onClose={close}
      title={t('release.partial.title')}
      closeLabel={t('release.partial.cancel')}
    >
      <form
        className={styles.section}
        data-testid="release-partial"
        onSubmit={(event) => {
          event.preventDefault();
          if (preview) onSend(preview.releasedCents);
          else check();
        }}
      >
        <p className={styles.body}>{t('release.partial.body')}</p>
        <Input
          label={t('release.partial.label')}
          hint={t('release.partial.hint', { price: money(priceCents) })}
          value={text}
          inputMode="decimal"
          onChange={(event) => {
            setText(event.target.value);
            setPreview(null);
          }}
          error={
            problem
              ? t(`release.partial.problems.${problem}`, { price: money(priceCents) })
              : undefined
          }
          data-testid="release-partial-amount"
        />
        {preview && (
          <dl className={styles.rows}>
            <dt>{t('release.partial.helperGets')}</dt>
            <dd data-testid="release-partial-helper">{money(preview.releasedCents)}</dd>
            <dt>{t('release.partial.keptFee')}</dt>
            <dd>{money(preview.keptFeeCents)}</dd>
            <dt className={styles.total}>{t('release.partial.refund')}</dt>
            <dd className={styles.total} data-testid="release-partial-refund">
              {money(preview.refundCents)}
            </dd>
          </dl>
        )}
        {failed && (
          <p className={`${styles.body} ${styles.error}`} role="alert">
            {t(failed)}
          </p>
        )}
        {preview ? (
          <Button
            type="submit"
            fullWidth
            disabled={busy}
            aria-busy={busy}
            data-testid="release-partial-send"
          >
            {t('release.partial.send')}
          </Button>
        ) : (
          <Button
            type="submit"
            fullWidth
            disabled={loading}
            aria-busy={loading}
            data-testid="release-partial-check"
          >
            {t('release.partial.check')}
          </Button>
        )}
        <Button type="button" variant="secondary" fullWidth onClick={close}>
          {t('release.partial.cancel')}
        </Button>
      </form>
    </Sheet>
  );
}

type TipPaymentState =
  | { kind: 'idle' }
  | { kind: 'starting'; amountCents: number }
  | { kind: 'form'; amountCents: number; clientSecret: string }
  | { kind: 'confirming' }
  | { kind: 'failed'; key: string };

/** Where the tip stands after the release (7f): the chips, the card form, or the thanks. */
function TipSection({
  state,
  picked,
  onPick,
  payment,
  money,
  onPay,
  form,
}: {
  state: TipState;
  picked: number | null;
  onPick(cents: number | null): void;
  payment: TipPaymentState;
  money(cents: number): string;
  onPay(cents: number): void;
  form(amountCents: number, clientSecret: string): ReactNode;
}) {
  const t = useTranslations();
  if (state.kind === 'closed') return null;
  if (state.kind === 'paid') {
    return (
      <Note icon={<InfoIcon size={18} />} data-testid="release-tip-paid">
        {t('release.tip.paid', { amount: money(state.amountCents) })}
      </Note>
    );
  }
  if (payment.kind === 'confirming') {
    return (
      <Note icon={<ClockIcon size={18} />} data-testid="release-tip-confirming">
        {t('release.tip.confirming')}
      </Note>
    );
  }
  if (payment.kind === 'form') {
    return (
      <Card className={styles.section} data-testid="release-tip-form">
        <h2 className={styles.h2}>{t('release.tip.title')}</h2>
        {form(payment.amountCents, payment.clientSecret)}
      </Card>
    );
  }
  const starting = payment.kind === 'starting';
  const failure =
    payment.kind === 'failed' ? (
      <p className={`${styles.body} ${styles.error}`} role="alert" data-testid="release-tip-error">
        {t(payment.key)}
      </p>
    ) : null;
  if (state.kind === 'pending') {
    return (
      <Card className={styles.section} data-testid="release-tip-pending">
        <p className={styles.body}>
          {t('release.tip.pending', { amount: money(state.amountCents) })}
        </p>
        {failure}
        <Button
          fullWidth
          disabled={starting}
          aria-busy={starting}
          onClick={() => onPay(state.amountCents)}
          data-testid="release-tip-pay"
        >
          {t('release.tip.pay', { amount: money(state.amountCents) })}
        </Button>
      </Card>
    );
  }
  if (state.choices.length === 0) return null;
  return (
    <Card className={styles.section} data-testid="release-tip">
      <TipChips
        legend={t('release.tip.title')}
        choices={state.choices}
        picked={picked}
        onPick={onPick}
        money={money}
        disabled={starting}
      />
      <p className={`${styles.small} ${styles.muted}`}>{t('release.tipNote')}</p>
      {failure}
      {picked !== null && (
        <Button
          fullWidth
          disabled={starting}
          aria-busy={starting}
          onClick={() => onPay(picked)}
          data-testid="release-tip-pay"
        >
          {t('release.tip.pay', { amount: money(picked) })}
        </Button>
      )}
    </Card>
  );
}

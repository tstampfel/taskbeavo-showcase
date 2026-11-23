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
} from '@taskbeavo/graphql';
import { defaultLocale, formatDateTime, formatMoney, isLocale, type Locale } from '@taskbeavo/i18n';
import { borderWidth, colors, minTapTarget, radii, spacing } from '@taskbeavo/ui-tokens';
import type { TFunction } from 'i18next';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import {
  AccessibilityInfo,
  ActivityIndicator,
  Image,
  Pressable,
  StyleSheet,
  View,
} from 'react-native';
import {
  AmountRow,
  AmountRowGroup,
  Button,
  Card,
  Heading,
  Input,
  Note,
  Screen,
  Sheet,
} from '../components/ui';
import { Text } from '../components/ui/text';
import { textStyle } from '../components/ui/text-style';
import type { PaymentSheetScopeComponent } from '../payments/checkout-route';
import type { PaymentSheet } from '../payments/stripe-config';

export interface ReleaseScreenProps {
  bookingId: string;
  onBack(): void;
  /** Provides Stripe's sheet (or the demo dialog) while a tip is paid; null without card payments. */
  PaymentScope: PaymentSheetScopeComponent;
}

/** The error text for a failed mutation: the release screen's own first, then the shared one. */
function errorText(t: TFunction, exists: (key: string) => boolean, error: unknown): string | null {
  if (!error) return null;
  const code = apiErrorCode(error);
  if (code && exists(`release.errors.${code}`)) return t(`release.errors.${code}`);
  if (code && exists(`errors.${code}`)) return t(`errors.${code}`);
  return t('release.loadError');
}

/**
 * Release payment (BEAV-118, mockups 7d–7f) for both sides. The poster sees the helper's
 * photos, note and invoice, releases the task price after a confirmation (7e), may pick a
 * tip first and pays it in Stripe's sheet right after (7f), or proposes a lower amount. The
 * helper answers that proposal here. Every amount and flag is the server's (releaseView);
 * a tip counts once Stripe's webhook confirms it (hard rule a), which arrives live.
 */
export function ReleaseScreen({ bookingId, onBack, PaymentScope }: ReleaseScreenProps) {
  const { t, i18n } = useTranslation();
  const locale: Locale = isLocale(i18n.language) ? i18n.language : defaultLocale;
  const exists = (key: string) => i18n.exists(key);
  const money = (cents: number) => formatMoney(cents, locale);
  const booking = useQuery(BookingDetailDocument, {
    variables: { id: bookingId },
    fetchPolicy: 'cache-and-network',
  });
  const viewerQuery = useQuery(ViewerDocument);
  const { subscribeToMore } = booking;
  useEffect(
    () => subscribeToBookingDetail(subscribeToMore, bookingId),
    [subscribeToMore, bookingId],
  );
  const variables = { bookingId };
  const [release, releasing] = useMutation(ReleasePaymentDocument);
  const [propose, proposing] = useMutation(ProposePartialReleaseDocument);
  const [withdraw, withdrawing] = useMutation(WithdrawPartialReleaseDocument);
  const [respond, responding] = useMutation(RespondToPartialReleaseDocument);
  const [tip, setTip] = useState<number | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [partialOpen, setPartialOpen] = useState(false);
  // The tip being paid in the sheet, and how the last attempt ended.
  const [payingTip, setPayingTip] = useState<number | null>(null);
  const [tipOutcome, setTipOutcome] = useState<TipOutcome | null>(null);

  const current = booking.data?.booking;
  const viewer = viewerQuery.data?.viewer;

  const frame = (children: ReactNode, footer?: ReactNode) => (
    <Screen
      title={t('release.title')}
      onBack={onBack}
      backLabel={t('release.back')}
      footer={footer}
      testID="release-screen"
    >
      {children}
    </Screen>
  );

  if (!current || (!viewer && viewerQuery.loading)) {
    return frame(
      booking.loading || viewerQuery.loading ? (
        <ActivityIndicator color={colors.green} accessibilityLabel={t('release.title')} />
      ) : (
        <Retry
          onRetry={() => {
            void booking.refetch().catch(() => undefined);
            void viewerQuery.refetch().catch(() => undefined);
          }}
        />
      ),
    );
  }
  const view = releaseView(current, bookingRole(current, viewer?.id ?? null));
  const actionError =
    errorText(t, exists, releasing.error) ??
    errorText(t, exists, proposing.error) ??
    errorText(t, exists, withdrawing.error) ??
    errorText(t, exists, responding.error);
  const errorLine = actionError && (
    <Text
      accessibilityRole="alert"
      style={[textStyle('body'), styles.error]}
      testID="release-error"
    >
      {actionError}
    </Text>
  );
  const back = (
    <Button variant="secondary" label={t('release.released.backToBooking')} onPress={onBack} />
  );

  const doRelease = () => {
    const chosen = tip;
    void release({ variables })
      .then(() => {
        setConfirming(false);
        AccessibilityInfo.announceForAccessibility(t('release.released.title'));
        if (chosen) {
          setTipOutcome(null);
          setPayingTip(chosen);
        }
      })
      .catch(() => setConfirming(false));
  };

  const confirmSheet = (
    <ConfirmSheet
      visible={confirming}
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
      return frame(<Retry onRetry={() => void viewerQuery.refetch().catch(() => undefined)} />);

    case 'release':
      return frame(
        <>
          <RequestSummary
            request={current.paymentRequest}
            title={current.task.title}
            time={(at) => (typeof at === 'string' ? formatDateTime(at, locale, 'short') : '')}
          />
          <AmountRowGroup>
            <AmountRow
              label={t('release.amount')}
              value={money(view.priceCents)}
              style={[textStyle('label'), styles.ink]}
              testID="release-amount"
            />
          </AmountRowGroup>
          {view.declined && (
            <Note tone="warn" testID="release-declined">
              {t('release.declined', { amount: money(view.declined.releasedCents) })}
            </Note>
          )}
          {view.tipChoices.length > 0 && (
            <TipChips
              legend={t('release.tipTitle')}
              choices={view.tipChoices}
              picked={tip}
              onPick={setTip}
              money={money}
            />
          )}
          {view.tipChoices.length > 0 && (
            <Text style={[textStyle('small'), styles.ink]}>{t('release.tipNote')}</Text>
          )}
          {current.task.onSiteContact && <Note>{t('release.checkFirst')}</Note>}
          {errorLine}
          {confirmSheet}
          <PartialSheet
            visible={partialOpen}
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
        </>,
        <>
          <Button
            label={t('release.submit')}
            onPress={() => setConfirming(true)}
            testID="release-submit"
          />
          <Button
            variant="ghost"
            size="sm"
            label={t('release.lower')}
            onPress={() => setPartialOpen(true)}
            testID="release-lower"
          />
        </>,
      );

    case 'proposalOpen':
      return frame(
        <>
          <Card testID="release-proposal-open">
            <Heading style={[textStyle('h2'), styles.ink]}>
              {t('release.proposalOpen.title')}
            </Heading>
            <Text style={[textStyle('body'), styles.ink]}>
              {t('release.proposalOpen.body', {
                released: money(view.releasedCents),
                price: money(view.priceCents),
              })}
            </Text>
            {view.refundCents != null && (
              <Text style={[textStyle('body'), styles.ink]}>
                {t('release.proposalOpen.refund', { refund: money(view.refundCents) })}
              </Text>
            )}
          </Card>
          {errorLine}
          {confirmSheet}
        </>,
        <>
          <Button
            variant="secondary"
            label={t('release.proposalOpen.withdraw')}
            busy={withdrawing.loading}
            disabled={withdrawing.loading}
            onPress={() => void withdraw({ variables }).catch(() => undefined)}
            testID="release-withdraw"
          />
          {view.canRelease && (
            <Button
              variant="ghost"
              size="sm"
              label={t('release.proposalOpen.releaseFull', { price: money(view.priceCents) })}
              onPress={() => {
                setTip(null);
                setConfirming(true);
              }}
              testID="release-full"
            />
          )}
        </>,
      );

    case 'partialSettled':
      return frame(
        <Card testID="release-partial-settled">
          <Heading style={[textStyle('h2'), styles.ink]}>
            {t('release.partialSettled.title')}
          </Heading>
          {current.helperUserId === viewer?.id ? (
            <Text style={[textStyle('body'), styles.ink]}>
              {t('release.partialSettled.helper', { released: money(view.releasedCents) })}
            </Text>
          ) : (
            <>
              <Text style={[textStyle('body'), styles.ink]}>
                {t('release.partialSettled.body', { released: money(view.releasedCents) })}
              </Text>
              {view.refundCents != null && (
                <Text style={[textStyle('body'), styles.ink]}>
                  {t(
                    view.done
                      ? 'release.partialSettled.refundDone'
                      : 'release.partialSettled.refundPending',
                    {
                      refund: money(view.refundCents),
                    },
                  )}
                </Text>
              )}
            </>
          )}
        </Card>,
        back,
      );

    case 'released':
      return frame(
        <>
          <Card testID="release-released">
            <Heading style={[textStyle('h1'), styles.green]}>{t('release.released.title')}</Heading>
            <Text style={[textStyle('body'), styles.ink]}>
              {t('release.released.body', { amount: money(view.priceCents) })}
            </Text>
            <AmountRowGroup>
              <AmountRow
                label={t('release.released.payout')}
                value={t('release.released.payoutTime')}
                style={[textStyle('small'), styles.ink]}
              />
            </AmountRowGroup>
          </Card>
          <TipSection
            state={view.tip}
            picked={tip}
            onPick={setTip}
            paying={payingTip !== null}
            outcome={tipOutcome}
            money={money}
            onPay={(cents) => {
              setTipOutcome(null);
              setPayingTip(cents);
            }}
          />
          {payingTip !== null && (
            <PaymentScope>
              {(sheet) => (
                <TipPayment
                  bookingId={bookingId}
                  amountCents={payingTip}
                  sheet={sheet}
                  onDone={(outcome) => {
                    setPayingTip(null);
                    setTipOutcome(outcome);
                    if (outcome.kind === 'paid') void booking.refetch().catch(() => undefined);
                  }}
                />
              )}
            </PaymentScope>
          )}
        </>,
        back,
      );

    case 'answer':
      return frame(
        <>
          <Card testID="release-answer">
            <Heading style={[textStyle('h2'), styles.ink]}>{t('release.answer.title')}</Heading>
            <Text style={[textStyle('body'), styles.ink]}>
              {t('release.answer.body', {
                released: money(view.releasedCents),
                price: money(view.priceCents),
              })}
            </Text>
          </Card>
          {errorLine}
        </>,
        <>
          <Button
            label={t('release.answer.accept', { released: money(view.releasedCents) })}
            busy={responding.loading}
            disabled={responding.loading}
            onPress={() =>
              void respond({ variables: { bookingId, accept: true } }).catch(() => undefined)
            }
            testID="release-accept"
          />
          <Button
            variant="secondary"
            label={t('release.answer.decline')}
            disabled={responding.loading}
            onPress={() =>
              void respond({ variables: { bookingId, accept: false } }).catch(() => undefined)
            }
            testID="release-decline"
          />
        </>,
      );

    case 'helperWaiting':
      return frame(
        <Note testID="release-helper-waiting">
          {t('release.helperWaiting', { amount: money(view.priceCents) })}
        </Note>,
        back,
      );

    case 'helperReleased':
      return frame(
        <Card testID="release-helper-released">
          <Text style={[textStyle('body'), styles.ink]}>
            {t('release.helperReleased', { amount: money(view.priceCents) })}
          </Text>
          {view.tipCents != null && (
            <Text style={[textStyle('body'), styles.ink]}>
              {t('release.helperTip', { amount: money(view.tipCents) })}
            </Text>
          )}
        </Card>,
        back,
      );

    case 'notRequested':
      return frame(<Note testID="release-not-requested">{t('release.notRequested')}</Note>, back);

    case 'unavailable':
      return frame(<Note testID="release-unavailable">{t('release.unavailable')}</Note>, back);
  }
}

function Retry({ onRetry }: { onRetry(): void }) {
  const { t } = useTranslation();
  return (
    <>
      <Text accessibilityRole="alert" style={[textStyle('body'), styles.error]}>
        {t('release.loadError')}
      </Text>
      <Button variant="secondary" label={t('release.retry')} onPress={onRetry} />
    </>
  );
}

/** Mockup 7d's top: the helper's photos, note and invoice from the payment request. */
function RequestSummary({
  request,
  title,
  time,
}: {
  request: PaymentRequestFieldsFragment | null;
  title: string;
  time(at: unknown): string;
}) {
  const { t } = useTranslation();
  return (
    <>
      <View style={styles.gap}>
        <Heading style={[textStyle('h2'), styles.ink]}>{t('release.finished')}</Heading>
        <Text style={[textStyle('small'), styles.muted]}>
          {request
            ? `${title} · ${t('release.requestedAt', { time: time(request.requestedAt) })}`
            : title}
        </Text>
      </View>
      {request && request.photos.length > 0 && (
        <View
          style={styles.photos}
          accessibilityLabel={t('release.photosTitle')}
          testID="release-photos"
        >
          {request.photos.map((photo, index) =>
            photo.url ? (
              <Image
                key={photo.id}
                source={{ uri: photo.url }}
                style={styles.photo}
                accessible
                accessibilityLabel={t('release.photoLabel', { number: index + 1 })}
              />
            ) : null,
          )}
        </View>
      )}
      {request?.note ? (
        <Card testID="release-note">
          <Text style={[textStyle('caption'), styles.muted]}>{t('release.noteTitle')}</Text>
          <Text style={[textStyle('body'), styles.ink]}>{request.note}</Text>
        </Card>
      ) : null}
      {request && (request.invoiceJir || request.invoice) && (
        <Card testID="release-invoice">
          <Text style={[textStyle('caption'), styles.muted]}>{t('release.invoiceTitle')}</Text>
          {request.invoiceJir && (
            <Text style={[textStyle('body'), styles.ink]} selectable>
              {t('release.invoiceJir', { jir: request.invoiceJir })}
            </Text>
          )}
          {request.invoice?.url && (
            <Image
              source={{ uri: request.invoice.url }}
              style={styles.invoice}
              resizeMode="contain"
              accessible
              accessibilityLabel={t('release.invoicePhoto')}
            />
          )}
        </Card>
      )}
    </>
  );
}

/** The tip chips of mockup 7d: none preselected, and "No tip". */
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
  const { t } = useTranslation();
  const options: { cents: number | null; label: string }[] = [
    ...choices.map((cents) => ({ cents, label: money(cents) })),
    { cents: null, label: t('release.tipNone') },
  ];
  return (
    <View accessibilityRole="radiogroup" accessibilityLabel={legend} style={styles.gap}>
      <Text style={[textStyle('label'), styles.ink]}>{legend}</Text>
      <View style={styles.chips}>
        {options.map(({ cents, label }) => {
          const on = picked === cents;
          return (
            <Pressable
              key={cents ?? 'none'}
              accessibilityRole="radio"
              accessibilityState={{ checked: on, disabled }}
              disabled={disabled}
              onPress={() => onPick(cents)}
              style={[styles.chip, on && styles.chipOn]}
              testID={`release-tip-${cents ?? 'none'}`}
            >
              <Text style={[textStyle('label'), on ? styles.onGreen : styles.ink]}>{label}</Text>
            </Pressable>
          );
        })}
      </View>
    </View>
  );
}

/** Mockup 7e: what goes to the helper, and that it can't be undone. */
function ConfirmSheet({
  visible,
  priceCents,
  tipCents,
  busy,
  money,
  onRelease,
  onClose,
}: {
  visible: boolean;
  priceCents: number;
  tipCents: number | null;
  busy: boolean;
  money(cents: number): string;
  onRelease(): void;
  onClose(): void;
}) {
  const { t } = useTranslation();
  const summary = releaseSummary(priceCents, tipCents);
  return (
    <Sheet
      visible={visible}
      onClose={onClose}
      title={t('release.confirm.title', { amount: money(summary.totalCents) })}
      closeLabel={t('release.confirm.cancel')}
      testID="release-confirm"
    >
      <AmountRowGroup>
        <AmountRow
          label={t('release.confirm.price')}
          value={money(summary.priceCents)}
          style={[textStyle('body'), styles.ink]}
        />
        {summary.tipCents > 0 && (
          <AmountRow
            label={t('release.confirm.tip')}
            value={money(summary.tipCents)}
            style={[textStyle('body'), styles.ink]}
            testID="release-confirm-tip"
          />
        )}
      </AmountRowGroup>
      <Text style={[textStyle('body'), styles.muted]}>{t('release.confirm.irreversible')}</Text>
      <Button
        label={t('release.confirm.release')}
        busy={busy}
        disabled={busy}
        onPress={onRelease}
        testID="release-confirm-release"
      />
      <Button variant="secondary" label={t('release.confirm.cancel')} onPress={onClose} />
    </Sheet>
  );
}

/** "Release a lower amount": the amount, then the server's preview, then the proposal. */
function PartialSheet({
  visible,
  bookingId,
  priceCents,
  busy,
  money,
  onClose,
  onSend,
}: {
  visible: boolean;
  bookingId: string;
  priceCents: number;
  busy: boolean;
  money(cents: number): string;
  onClose(): void;
  onSend(releasedCents: number): void;
}) {
  const { t, i18n } = useTranslation();
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
      .catch((error: unknown) => setFailed(errorText(t, (key) => i18n.exists(key), error)))
      .finally(() => setLoading(false));
  };

  return (
    <Sheet
      visible={visible}
      onClose={close}
      title={t('release.partial.title')}
      closeLabel={t('release.partial.cancel')}
      testID="release-partial"
    >
      <Text style={[textStyle('body'), styles.ink]}>{t('release.partial.body')}</Text>
      <Input
        label={t('release.partial.label')}
        hint={t('release.partial.hint', { price: money(priceCents) })}
        value={text}
        onChangeText={(value) => {
          setText(value);
          setPreview(null);
        }}
        keyboardType="decimal-pad"
        error={
          problem
            ? t(`release.partial.problems.${problem}`, { price: money(priceCents) })
            : undefined
        }
        testID="release-partial-amount"
      />
      {preview && (
        <AmountRowGroup>
          <AmountRow
            label={t('release.partial.helperGets')}
            value={money(preview.releasedCents)}
            style={[textStyle('body'), styles.ink]}
            testID="release-partial-helper"
          />
          <AmountRow
            label={t('release.partial.keptFee')}
            value={money(preview.keptFeeCents)}
            style={[textStyle('body'), styles.ink]}
          />
          <AmountRow
            label={t('release.partial.refund')}
            value={money(preview.refundCents)}
            style={[textStyle('label'), styles.ink]}
            testID="release-partial-refund"
          />
        </AmountRowGroup>
      )}
      {failed && (
        <Text accessibilityRole="alert" style={[textStyle('body'), styles.error]}>
          {failed}
        </Text>
      )}
      {preview ? (
        <Button
          label={t('release.partial.send')}
          busy={busy}
          disabled={busy}
          onPress={() => onSend(preview.releasedCents)}
          testID="release-partial-send"
        />
      ) : (
        <Button
          label={t('release.partial.check')}
          busy={loading}
          disabled={loading}
          onPress={check}
          testID="release-partial-check"
        />
      )}
      <Button variant="secondary" label={t('release.partial.cancel')} onPress={close} />
    </Sheet>
  );
}

type TipOutcome = { kind: 'paid' } | { kind: 'closed' } | { kind: 'failed'; message: string };

/** Where the tip stands after the release (7f), and the chips while none is paid. */
function TipSection({
  state,
  picked,
  onPick,
  paying,
  outcome,
  money,
  onPay,
}: {
  state: Extract<ReturnType<typeof releaseView>, { kind: 'released' }>['tip'];
  picked: number | null;
  onPick(cents: number | null): void;
  paying: boolean;
  outcome: TipOutcome | null;
  money(cents: number): string;
  onPay(cents: number): void;
}) {
  const { t } = useTranslation();
  if (state.kind === 'closed') return null;
  if (state.kind === 'paid') {
    return (
      <Note testID="release-tip-paid">
        {t('release.tip.paid', { amount: money(state.amountCents) })}
      </Note>
    );
  }
  const outcomeLine =
    outcome?.kind === 'failed' ? (
      <Text
        accessibilityRole="alert"
        style={[textStyle('body'), styles.error]}
        testID="release-tip-error"
      >
        {outcome.message}
      </Text>
    ) : outcome?.kind === 'closed' ? (
      <Text style={[textStyle('body'), styles.ink]}>{t('release.tip.failed')}</Text>
    ) : null;
  if (outcome?.kind === 'paid') {
    return <Note testID="release-tip-confirming">{t('release.tip.confirming')}</Note>;
  }
  if (state.kind === 'pending') {
    return (
      <Card testID="release-tip-pending">
        <Text style={[textStyle('body'), styles.ink]}>
          {t('release.tip.pending', { amount: money(state.amountCents) })}
        </Text>
        {outcomeLine}
        <Button
          label={t('release.tip.pay', { amount: money(state.amountCents) })}
          busy={paying}
          disabled={paying}
          onPress={() => onPay(state.amountCents)}
          testID="release-tip-pay"
        />
      </Card>
    );
  }
  if (state.choices.length === 0) return null;
  return (
    <Card testID="release-tip">
      <TipChips
        legend={t('release.tip.title')}
        choices={state.choices}
        picked={picked}
        onPick={onPick}
        money={money}
        disabled={paying}
      />
      <Text style={[textStyle('small'), styles.ink]}>{t('release.tipNote')}</Text>
      {outcomeLine}
      {picked !== null && (
        <Button
          label={t('release.tip.pay', { amount: money(picked) })}
          busy={paying}
          disabled={paying}
          onPress={() => onPay(picked)}
          testID="release-tip-pay"
        />
      )}
    </Card>
  );
}

/**
 * Pays one tip, once, when mounted: addTip for the client secret (never cached or stored),
 * then the sheet. "Paid" only means Stripe took it; the tip counts when the webhook says so.
 */
function TipPayment({
  bookingId,
  amountCents,
  sheet,
  onDone,
}: {
  bookingId: string;
  amountCents: number;
  sheet: PaymentSheet | null;
  onDone(outcome: TipOutcome): void;
}) {
  const { t, i18n } = useTranslation();
  const [addTip] = useMutation(AddTipDocument, { fetchPolicy: 'no-cache' });
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    if (!sheet) {
      onDone({ kind: 'failed', message: t('release.tip.notConfigured') });
      return;
    }
    void addTip({ variables: { bookingId, amountCents } })
      .then(async ({ data }) => {
        const secret = data?.addTip.clientSecret;
        if (!secret) throw new Error('no client secret');
        const result = await sheet.pay(secret, t('release.tip.sheetButton'));
        onDone(
          result === 'paid'
            ? { kind: 'paid' }
            : result === 'closed'
              ? { kind: 'closed' }
              : { kind: 'failed', message: t('release.tip.failed') },
        );
      })
      .catch((error: unknown) =>
        onDone({
          kind: 'failed',
          message: errorText(t, (key) => i18n.exists(key), error) ?? t('release.tip.failed'),
        }),
      );
  }, [addTip, amountCents, bookingId, i18n, onDone, sheet, t]);
  return <ActivityIndicator color={colors.green} accessibilityLabel={t('release.tip.title')} />;
}

const styles = StyleSheet.create({
  ink: { color: colors.ink },
  green: { color: colors.green },
  muted: { color: colors.inkMuted },
  error: { color: colors.error },
  onGreen: { color: colors.white },
  gap: { gap: spacing.sm },
  photos: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  photo: { width: 100, height: 100, borderRadius: radii.md, backgroundColor: colors.line },
  invoice: { width: '100%', height: 200, borderRadius: radii.md },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  chip: {
    minHeight: minTapTarget,
    minWidth: 64,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: spacing.lg,
    borderRadius: radii.pill,
    borderWidth: borderWidth.thin,
    borderColor: colors.line,
    backgroundColor: colors.white,
  },
  chipOn: { borderColor: colors.green, backgroundColor: colors.green },
});

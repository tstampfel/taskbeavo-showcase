import { useMutation, useQuery } from '@apollo/client/react';
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
import { defaultLocale, formatDate, formatMoney, isLocale, type Locale } from '@taskbeavo/i18n';
import { borderWidth, colors, minTapTarget, radii, spacing } from '@taskbeavo/ui-tokens';
import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ActivityIndicator, Pressable, StyleSheet, View } from 'react-native';
import { Text } from '../components/ui/text';
import {
  AmountRow,
  Button,
  CARD_HEADING_INSET,
  Card,
  Heading,
  Input,
  Note,
  Screen,
} from '../components/ui';
import { textStyle } from '../components/ui/text-style';

export interface CancelScreenProps {
  bookingId: string;
  onOpenPolicy(locale: Locale): void;
  onBack(): void;
}

/**
 * The cancel screen for both sides of a booking (mockup 13). The poster sees the refund
 * preview and the withdrawal button; the helper sees what their cancel means for the poster
 * (13g). Either side may cite a no-fault reason, which the other side answers (13f). Every
 * amount is the server's (bookings/cancellation.py); the mutations work it out again. The
 * withdrawal button stays while the ordinary cancel is refused with WORK_STARTED.
 */
export function CancelScreen({ bookingId, onOpenPolicy, onBack }: CancelScreenProps) {
  const { t, i18n } = useTranslation();
  const locale: Locale = isLocale(i18n.language) ? i18n.language : defaultLocale;
  const money = (cents: number) => formatMoney(cents, locale);
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

  const frame = (children: ReactNode, footer?: ReactNode) => (
    <Screen
      title={t('cancel.title')}
      onBack={onBack}
      backLabel={t('cancel.back')}
      footer={footer}
      testID="cancel-screen"
    >
      {children}
    </Screen>
  );

  if (!current || (!viewer && viewerQuery.loading)) {
    return frame(
      <>
        {booking.loading || viewerQuery.loading ? (
          <ActivityIndicator color={colors.green} />
        ) : (
          <>
            <Text accessibilityRole="alert" style={[textStyle('body'), styles.error]}>
              {t('cancel.loadError')}
            </Text>
            <Button
              variant="secondary"
              label={t('cancel.retry')}
              onPress={() => void booking.refetch().catch(() => undefined)}
            />
          </>
        )}
      </>,
    );
  }

  const role = cancelRole(current, viewer?.id ?? null);
  const view = cancelView(current, role);
  const decision = noFaultDecision(current);
  const mutations = [cancelling, helperCancelling, requesting, responding, reopening, withdrawing];
  const error = mutations.find((m) => m.error)?.error;
  const errorCode = error ? apiErrorCode(error) : undefined;
  const busy = mutations.some((m) => m.loading);
  const refresh = () => void booking.refetch().catch(() => undefined);
  const onNoFault = (picked: PickedReason & { kind: 'noFault' }) =>
    void requestNoFault({ variables: { id: bookingId, reason: picked.key } }).catch(refresh);
  if (view.kind === 'unknownViewer') {
    // Never show one side's screen to someone whose side isn't known (R1-11).
    return frame(
      <>
        <Text accessibilityRole="alert" style={[textStyle('body'), styles.error]}>
          {t('cancel.loadError')}
        </Text>
        <Button
          variant="secondary"
          label={t('cancel.retry')}
          onPress={() => void viewerQuery.refetch().catch(() => undefined)}
        />
      </>,
    );
  }
  const onWithdraw = (name: string, email: string) =>
    void withdraw({ variables: { id: bookingId, name, email } }).catch(() => undefined);

  // The screen's one final action. The helper's is pinned at the bottom (mockup 13g `.foot`):
  // what it means for the poster is in the card at the top. The poster's stays in the body,
  // right under the refund breakdown, so it can't be tapped with the amounts below the fold
  // (unlike mockup 13c).
  let footer: ReactNode;
  if ((view.kind === 'cancel' || view.kind === 'helperCancel') && reason?.kind === 'noFault') {
    footer = (
      <Button
        variant="danger"
        label={t('cancel.noFaultRequest.send')}
        busy={busy}
        disabled={busy}
        onPress={() => onNoFault(reason)}
        testID="no-fault-send"
      />
    );
  } else if (view.kind === 'helperCancel') {
    footer = (
      <Button
        variant="danger"
        label={t('cancel.helper.confirm')}
        busy={helperCancelling.loading}
        disabled={reason === null || busy}
        onPress={() => {
          if (reason?.kind !== 'ordinary') return;
          const key = reason.key as HelperCancelReason;
          void cancelAsHelper({ variables: { id: bookingId, reason: key } }).catch(refresh);
        }}
        testID="helper-cancel-confirm"
      />
    );
  } else if (view.kind === 'cancel') {
    footer = (
      <Button
        variant="danger"
        label={t('cancel.confirm')}
        busy={cancelling.loading}
        disabled={reason === null || busy}
        onPress={() => {
          if (reason?.kind !== 'ordinary') return;
          const key = reason.key as PosterCancelReason;
          const expectedRefundCents = view.preview.refundCents;
          void cancel({
            variables: { id: bookingId, reason: key, expectedRefundCents },
          }).catch((e: unknown) => {
            // A deadline passed while the screen was open: show the new preview.
            if (apiErrorCode(e) === 'CANCELLATION_CHANGED') {
              void booking.refetch().catch(() => undefined);
            }
          });
        }}
        testID="cancel-confirm"
      />
    );
  }

  return frame(
    <>
      {decision && (
        <Text style={[textStyle('body'), styles.ink]} testID="no-fault-decision">
          {t(`cancel.decision.${decision}`)}
        </Text>
      )}

      {view.kind === 'done' && (
        <Card style={styles.card}>
          <Heading style={[textStyle('h2'), styles.ink]}>
            {view.withdrawal
              ? t('cancel.done.withdrawn')
              : view.byHelper && role === 'poster'
                ? t('cancel.done.byHelper')
                : t('cancel.done.cancelled')}
          </Heading>
          {view.awaitingSupport ? (
            <Text style={[textStyle('body'), styles.ink]}>{t('cancel.done.awaitingSupport')}</Text>
          ) : (
            view.refundCents !== null && (
              <Text style={[textStyle('body'), styles.ink]}>
                {view.refunding
                  ? t('cancel.done.refunding', { amount: money(view.refundCents) })
                  : t('cancel.done.refunded', { amount: money(view.refundCents) })}
              </Text>
            )
          )}
          {view.withdrawal && (
            <Text style={[textStyle('small'), styles.muted]}>{t('cancel.done.emailed')}</Text>
          )}
          {role === 'poster' && view.creditBackCents > 0 && (
            <Text style={[textStyle('body'), styles.ink]} testID="cancel-credit-back">
              {t('cancel.done.creditBack', { amount: money(view.creditBackCents) })}
            </Text>
          )}
          {role === 'poster' && view.posterCreditCents > 0 && (
            <Text style={[textStyle('body'), styles.ink]} testID="cancel-credit">
              {t('cancel.done.credit', { amount: money(view.posterCreditCents) })}
            </Text>
          )}
          {view.reopened && role === 'poster' && (
            <Text style={[textStyle('body'), styles.ink]}>{t('cancel.done.reopened')}</Text>
          )}
          {view.canReopen && (
            <>
              <Text style={[textStyle('small'), styles.muted]}>{t('cancel.done.reopenBody')}</Text>
              <Button
                variant="secondary"
                label={t('cancel.done.reopen')}
                busy={reopening.loading}
                disabled={busy}
                onPress={() => void reopen({ variables: { bookingId } }).catch(refresh)}
                testID="cancel-reopen"
              />
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
          <Card style={styles.card}>
            <Heading style={[textStyle('h2'), styles.ink]}>{t('cancel.helper.title')}</Heading>
            <Text style={[textStyle('body'), styles.ink]} testID="helper-cancel-warning">
              {view.posterCreditCents > 0
                ? t('cancel.helper.warningCredit', {
                    refund: money(view.refundCents),
                    credit: money(view.posterCreditCents),
                  })
                : t('cancel.helper.warning', { refund: money(view.refundCents) })}
            </Text>
            <Text style={[textStyle('small'), styles.muted]}>{t('cancel.helper.neverPay')}</Text>
          </Card>
          <Reasons
            legend={t('cancel.helper.reasonsLegend')}
            ordinary={HELPER_CANCEL_REASONS}
            ordinaryLabel={(key) => t(`cancel.helper.reasons.${key}`)}
            noFault={view.noFault}
            picked={reason}
            onPick={setReason}
          />
          <Text style={[textStyle('small'), styles.muted]}>{t('cancel.helper.tip')}</Text>
          {reason?.kind === 'noFault' && <NoFaultNotice role={role} />}
        </>
      )}

      {view.kind === 'cancel' && (
        <>
          <Reasons
            legend={t('cancel.reasonsLegend')}
            ordinary={POSTER_CANCEL_REASONS}
            ordinaryLabel={(key) => t(`cancel.reasons.${key}`)}
            noFault={view.noFault}
            noFaultHint={t('cancel.noFault')}
            picked={reason}
            onPick={setReason}
          />
          {reason?.kind === 'noFault' ? (
            <NoFaultNotice role={role} />
          ) : (
            <Preview preview={view.preview} money={money} />
          )}
          {footer}
        </>
      )}

      {(view.kind === 'refused' || view.kind === 'withdrawOnly') && (
        <Text style={[textStyle('body'), styles.ink]}>
          {role === 'helper'
            ? t('cancel.helper.refused')
            : view.refusedWith === 'WORK_STARTED'
              ? t('cancel.refused.WORK_STARTED')
              : t('cancel.refused.BOOKING_ACTION_NOT_ALLOWED')}
        </Text>
      )}

      {view.kind === 'unavailable' && (
        <Text style={[textStyle('body'), styles.ink]}>{t('cancel.unavailable')}</Text>
      )}

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
        <Text accessibilityRole="alert" style={[textStyle('body'), styles.error]}>
          {i18n.exists(`errors.${errorCode}`) ? t(`errors.${errorCode}`) : t('cancel.loadError')}
        </Text>
      )}

      <Button
        variant="ghost"
        size="sm"
        label={t('cancel.policyLink')}
        onPress={() => onOpenPolicy(locale)}
      />
    </>,
    view.kind === 'cancel' ? undefined : footer,
  );
}

/** The ordinary reasons and, when allowed, the no-fault ones: one radio group (13c, 13g). */
function Reasons<K extends string>({
  legend,
  ordinary,
  ordinaryLabel,
  noFault,
  noFaultHint,
  picked,
  onPick,
}: {
  legend: string;
  ordinary: readonly K[];
  ordinaryLabel(key: K): string;
  noFault: boolean;
  /** Said just above the no-fault reasons. */
  noFaultHint?: string;
  picked: PickedReason | null;
  onPick(reason: PickedReason): void;
}) {
  const { t } = useTranslation();
  const radio = (reason: PickedReason, label: string, index: number) => {
    const checked = picked?.kind === reason.kind && picked.key === reason.key;
    return (
      <Pressable
        key={`${reason.kind}-${reason.key}`}
        accessibilityRole="radio"
        accessibilityState={{ checked }}
        onPress={() => onPick(reason)}
        style={[styles.reason, index > 0 && styles.reasonRule]}
        testID={`cancel-reason-${reason.key}`}
      >
        <View
          style={[styles.dot, checked && styles.dotChecked]}
          testID={`cancel-reason-dot-${reason.key}`}
        />
        <Text style={[textStyle('body'), styles.ink, styles.flex]} inset={REASON_LABEL_INSET}>
          {label}
        </Text>
      </Pressable>
    );
  };
  return (
    <>
      <Heading style={[textStyle('h2'), styles.ink]}>{legend}</Heading>
      <Card accessibilityRole="radiogroup" style={styles.reasons}>
        {ordinary.map((key, index) =>
          radio({ kind: 'ordinary', key } as PickedReason, ordinaryLabel(key), index),
        )}
      </Card>
      {noFault && (
        <>
          {noFaultHint ? (
            <Text style={[textStyle('small'), styles.muted]}>{noFaultHint}</Text>
          ) : null}
          <Heading style={[textStyle('label'), styles.ink]}>{t('cancel.noFaultLegend')}</Heading>
          <Card accessibilityRole="radiogroup" style={styles.reasons}>
            {NO_FAULT_REASONS.map((key, index) =>
              radio({ kind: 'noFault', key }, t(`cancel.noFaultReasons.${key}`), index),
            )}
          </Card>
        </>
      )}
    </>
  );
}

/** What a no-fault request means; the footer sends it. */
function NoFaultNotice({ role }: { role: CancelRole }) {
  const { t } = useTranslation();
  return <Note>{t(`cancel.noFaultRequest.${role}`)}</Note>;
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
  const { t } = useTranslation();
  // The other side sees only the generic label (GDPR Art. 9, decision 4).
  const reason =
    pending.requestedByYou && pending.reason
      ? t(`cancel.noFaultReasons.${pending.reason}`)
      : t(`cancel.noFaultLabels.${pending.label}`);
  const asked = pending.requestedByYou
    ? t('cancel.pending.youAsked', { reason })
    : role === 'helper'
      ? t('cancel.pending.posterAsked', { reason })
      : t('cancel.pending.helperAsked', { reason });
  return (
    <Card style={styles.card} testID="no-fault-pending">
      <Heading style={[textStyle('h2'), styles.ink]}>
        {pending.awaitingSupport
          ? t('cancel.pending.underReview')
          : t('cancel.pending.hoursLeft', { hours: hoursLeft(pending.respondBy) })}
      </Heading>
      <Text style={[textStyle('body'), styles.ink]}>{asked}</Text>
      {pending.awaitingSupport && (
        <Text style={[textStyle('body'), styles.ink]}>{t('cancel.pending.underReviewBody')}</Text>
      )}
      {/* The poster may still answer a helper's request until support decides. */}
      {pending.canRespond ? (
        <>
          <Text style={[textStyle('body'), styles.ink]}>
            {role === 'helper'
              ? t('cancel.pending.helperAnswer')
              : pending.awaitingSupport
                ? t('cancel.pending.posterAnswerUnderReview')
                : t('cancel.pending.posterAnswer')}
          </Text>
          <Button
            label={t('cancel.pending.agree')}
            disabled={busy}
            onPress={() => onRespond(true)}
            testID="no-fault-agree"
          />
          <Button
            variant="secondary"
            label={t('cancel.pending.disagree')}
            disabled={busy}
            onPress={() => onRespond(false)}
            testID="no-fault-disagree"
          />
        </>
      ) : (
        !pending.awaitingSupport && (
          <Text style={[textStyle('small'), styles.muted]}>{t('cancel.pending.waiting')}</Text>
        )
      )}
    </Card>
  );
}

function Preview({ preview, money }: { preview: CancelPreview; money: (cents: number) => string }) {
  const { t } = useTranslation();
  return (
    <Card selected style={styles.card} testID="cancel-preview">
      <Heading style={[textStyle('label'), styles.ink]}>
        {preview.full ? t('cancel.preview.full') : t('cancel.preview.title')}
      </Heading>
      <Text style={[textStyle('small'), styles.muted]}>{t(`cancel.cases.${preview.case}`)}</Text>
      <Row label={t('cancel.preview.youGetBack')} value={money(preview.refundCents)} strong />
      {preview.helperCompensationCents > 0 && (
        <>
          <Row
            label={t('cancel.preview.helperKeeps')}
            value={money(preview.helperCompensationCents)}
          />
          <Row label={t('cancel.preview.keptFee')} value={money(preview.keptFeeCents)} />
        </>
      )}
      <View style={styles.rule} />
      <Row label={t('cancel.preview.youPaid')} value={money(preview.chargeCents)} muted />
      {preview.creditBackCents > 0 && (
        <Text style={[textStyle('body'), styles.ink]} testID="cancel-preview-credit-back">
          {t('cancel.preview.creditBack', { amount: money(preview.creditBackCents) })}
        </Text>
      )}
      <Text style={[textStyle('small'), styles.muted]}>{t('cancel.preview.toCard')}</Text>
    </Card>
  );
}

function Row({
  label,
  value,
  strong,
  muted,
}: {
  label: string;
  value: string;
  strong?: boolean;
  muted?: boolean;
}) {
  return (
    <AmountRow
      label={label}
      value={value}
      style={[textStyle(strong ? 'label' : 'small'), muted ? styles.muted : styles.ink]}
      valueStyle={strong && styles.amount}
    />
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
  onWithdraw(name: string, email: string): void;
}) {
  const { t } = useTranslation();
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
    <Card style={styles.card}>
      <Heading style={[textStyle('h2'), styles.ink]}>{t('cancel.withdrawal.title')}</Heading>
      {offer.endsAt && (
        <Text style={[textStyle('small'), styles.ink]}>
          {t('cancel.withdrawal.until', { date: lastDay(offer.endsAt, locale) })}
        </Text>
      )}
      <Text style={[textStyle('body'), styles.ink]}>
        {started ? t('cancel.withdrawal.afterStart') : t('cancel.withdrawal.beforeStart')}
      </Text>
      {confirming ? (
        <View style={styles.card}>
          <Text style={[textStyle('body'), styles.ink, styles.strong]}>
            {t('cancel.withdrawal.confirmTitle')}
          </Text>
          <Text style={[textStyle('body'), styles.ink]}>{t('cancel.withdrawal.confirmBody')}</Text>
          <Text style={[textStyle('small'), styles.ink]}>
            {t('cancel.withdrawal.contract')}: {bookingId}
          </Text>
          <Input
            label={t('cancel.withdrawal.name')}
            value={name}
            autoComplete="name"
            onChangeText={setName}
            testID="withdraw-name"
          />
          <Input
            label={t('cancel.withdrawal.email')}
            hint={t('cancel.withdrawal.formIntro')}
            value={email}
            autoComplete="email"
            keyboardType="email-address"
            autoCapitalize="none"
            onChangeText={setEmail}
            testID="withdraw-email"
          />
          <Button
            variant="danger"
            label={t('cancel.withdrawal.confirm')}
            busy={sending}
            disabled={busy || !name.trim() || !email.trim()}
            onPress={() => onWithdraw(name, email)}
            testID="withdraw-confirm"
          />
          <Button
            variant="ghost"
            label={t('cancel.withdrawal.keep')}
            disabled={busy}
            onPress={() => setConfirming(false)}
          />
        </View>
      ) : (
        <Button
          variant="secondary"
          label={t('cancel.withdrawal.button')}
          disabled={busy}
          onPress={start}
          testID="withdraw-from-contract"
        />
      )}
    </Card>
  );
}

/** The period ends at 24:00 Zagreb time: the last day is the one before that midnight. */
function lastDay(endsAt: string, locale: Locale): string {
  return formatDate(new Date(new Date(endsAt).getTime() - 1), locale, 'long');
}

const RADIO_DOT = 24;
/** The width beside a reason's label: the screen and card padding, the radio and its gap. */
const REASON_LABEL_INSET = CARD_HEADING_INSET + RADIO_DOT + spacing.md;

const styles = StyleSheet.create({
  ink: { color: colors.ink },
  muted: { color: colors.inkMuted },
  error: { color: colors.error },
  strong: { fontWeight: '700' },
  // The amount the poster gets back: green, like the mockups' `.ok`, in the AA-safe brand green.
  amount: { color: colors.green },
  flex: { flex: 1 },
  card: { gap: spacing.sm },
  rule: { height: borderWidth.thin, backgroundColor: colors.line },
  // The mockups' list card: one card, the options split by hairlines.
  reasons: { gap: 0, paddingVertical: spacing.xs },
  reason: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    minHeight: minTapTarget + spacing.sm,
    paddingVertical: spacing.sm,
  },
  reasonRule: { borderTopWidth: borderWidth.thin, borderTopColor: colors.line },
  // The mockups' `.radio`: an outlined circle, filled by a thick green ring when picked.
  dot: {
    width: RADIO_DOT,
    height: RADIO_DOT,
    // A wrapping label at large text must not squeeze the circle.
    flexShrink: 0,
    borderRadius: radii.pill,
    borderWidth: 2,
    borderColor: colors.inkMuted,
    backgroundColor: colors.white,
  },
  dotChecked: { borderWidth: 7, borderColor: colors.green },
});

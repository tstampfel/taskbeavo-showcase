import { useMutation, useQuery } from '@apollo/client/react';
import {
  BookingArrivalDocument,
  CancelAsHelperDocument,
  ConfirmHelperArrivalDocument,
  ContestNoShowDocument,
  ETA_CHOICES,
  MarkArrivedDocument,
  MarkOnMyWayDocument,
  ReportRunningLateDocument,
  RequestEarlyStartDocument,
  RespondToEarlyStartDocument,
  RespondToLateArrivalDocument,
  ViewerDocument,
  apiErrorCode,
  arrivalRole,
  arrivalStatusMessage,
  arrivalView,
  graphqlLocale,
  type HelperArrival,
  type PosterArrival,
} from '@taskbeavo/graphql';
import {
  defaultLocale,
  formatDateTime,
  formatMoney,
  formatTime,
  isLocale,
  type Locale,
} from '@taskbeavo/i18n';
import { borderWidth, colors, minTapTarget, radii, spacing } from '@taskbeavo/ui-tokens';
import type { TFunction } from 'i18next';
import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ActivityIndicator, Pressable, StyleSheet, View } from 'react-native';
import { Text } from '../components/ui/text';
import { Button, Card, Heading, Note, Screen } from '../components/ui';
import { textStyle } from '../components/ui/text-style';

export interface ArrivalScreenProps {
  bookingId: string;
  onBack(): void;
}

/**
 * The day of the job for both sides (mockup 18). The helper taps On my way, Running late
 * and I've arrived (evidence only), or "I can't come" (the helper cancellation), and asks
 * to start early; the poster answers a late report, "Did your helper come?" and an early
 * start. A "No" waits 24 hours for the helper's "I was there" before any refund. Which
 * buttons show comes from the server's `can*` flags; the mutations check again.
 */
export function ArrivalScreen({ bookingId, onBack }: ArrivalScreenProps) {
  const { t, i18n } = useTranslation();
  const locale: Locale = isLocale(i18n.language) ? i18n.language : defaultLocale;
  const booking = useQuery(BookingArrivalDocument, {
    variables: { id: bookingId },
    fetchPolicy: 'cache-and-network',
  });
  const viewerQuery = useQuery(ViewerDocument);
  const [onMyWay, goingOut] = useMutation(MarkOnMyWayDocument);
  const [late, reportingLate] = useMutation(ReportRunningLateDocument);
  const [arrived, arriving] = useMutation(MarkArrivedDocument);
  const [cantCome, cancelling] = useMutation(CancelAsHelperDocument);
  const [answerLate, answeringLate] = useMutation(RespondToLateArrivalDocument);
  const [confirmCame, confirming] = useMutation(ConfirmHelperArrivalDocument);
  const [contest, contesting] = useMutation(ContestNoShowDocument);
  const [startEarly, askingEarly] = useMutation(RequestEarlyStartDocument);
  const [answerEarly, answeringEarly] = useMutation(RespondToEarlyStartDocument);
  const [helperState, setHelperState] = useState<HelperState>(HELPER_IDLE);
  const current = booking.data?.booking;
  const viewer = viewerQuery.data?.viewer;

  const retry = (
    <>
      <Text accessibilityRole="alert" style={[textStyle('body'), styles.error]}>
        {t('arrival.loadError')}
      </Text>
      <Button
        variant="secondary"
        label={t('arrival.retry')}
        onPress={() => {
          void booking.refetch().catch(() => undefined);
          void viewerQuery.refetch().catch(() => undefined);
        }}
      />
    </>
  );
  const frame = (children: ReactNode, footer?: ReactNode) => (
    <Screen
      title={t('arrival.title')}
      onBack={onBack}
      backLabel={t('arrival.back')}
      footer={footer}
      testID="arrival-screen"
    >
      {children}
    </Screen>
  );
  if (!current || (!viewer && viewerQuery.loading)) {
    return frame(
      booking.loading || viewerQuery.loading ? <ActivityIndicator color={colors.green} /> : retry,
    );
  }
  const view = arrivalView(current, arrivalRole(current, viewer?.id ?? null));
  if (view.kind === 'unknownViewer') {
    // Never show one side's screen to someone whose side isn't known.
    return frame(retry);
  }

  const mutations = [
    goingOut,
    reportingLate,
    arriving,
    cancelling,
    answeringLate,
    confirming,
    contesting,
    askingEarly,
    answeringEarly,
  ];
  const errorCode = (() => {
    const error = mutations.find((m) => m.error)?.error;
    return error ? apiErrorCode(error) : undefined;
  })();
  const busy = mutations.some((m) => m.loading);
  const refresh = () => void booking.refetch().catch(() => undefined);
  const time = (at: string | null) => (at ? formatDateTime(at, locale, 'short') : '');
  const vars = { bookingId };
  // With an ETA the line says when the helper expects to arrive, without one when they set
  // off (BEAV-202); both are clock times on the day of the job.
  const status = arrivalStatusMessage(view);
  const statusTime = status.at ? formatTime(status.at, locale) : '';

  const helper =
    view.kind === 'helper'
      ? helperActions({
          view,
          busy,
          state: helperState,
          setState: setHelperState,
          t,
          onMyWay: (etaMinutes) =>
            void onMyWay({ variables: { ...vars, etaMinutes } }).catch(refresh),
          onLate: (etaMinutes) => void late({ variables: { ...vars, etaMinutes } }).catch(refresh),
          onArrived: () => void arrived({ variables: vars }).catch(refresh),
          onCantCome: () =>
            void cantCome({ variables: { id: bookingId, reason: 'CANT_COME' } })
              .then(refresh)
              .catch(refresh),
          onStartEarly: () => void startEarly({ variables: vars }).catch(refresh),
        })
      : null;

  return frame(
    <>
      {/* The day of the job at a glance (mockup 18a, 18g): when, and where things stand. */}
      <Card style={styles.card} testID="arrival-summary">
        <Text style={[textStyle('caption'), styles.muted, styles.section]}>
          {typeof current.slotStart === 'string' ? time(current.slotStart) : null}
        </Text>
        <Text style={[textStyle('label'), styles.ink]} testID="arrival-status">
          {view.kind === 'helper'
            ? t(`arrival.statusHelper.${status.key}`, { time: statusTime })
            : t(`arrival.statusPoster.${status.key}`, { time: statusTime })}
        </Text>
      </Card>

      {view.noShowRejected && (
        <Text style={[textStyle('body'), styles.ink]}>{t('arrival.rejected')}</Text>
      )}
      {view.earlyStartConfirmed && (
        <Text style={[textStyle('body'), styles.ink]}>{t('arrival.earlyStartConfirmed')}</Text>
      )}

      {view.noShow && (
        <Card style={[styles.card, styles.alert]} testID="arrival-no-show">
          <Text style={[textStyle('body'), styles.ink]}>
            {view.noShow.settled
              ? t(view.kind === 'helper' ? 'arrival.settledHelper' : 'arrival.settledPoster')
              : view.noShow.contested
                ? t(view.kind === 'helper' ? 'arrival.contestedHelper' : 'arrival.contestedPoster')
                : t(view.kind === 'helper' ? 'arrival.reportedHelper' : 'arrival.reportedPoster', {
                    time: time(view.noShow.contestBy),
                  })}
          </Text>
          {view.kind === 'helper' && view.canContestNoShow && (
            <Button
              label={t('arrival.contest')}
              busy={contesting.loading}
              disabled={busy}
              onPress={() => void contest({ variables: vars }).catch(refresh)}
              testID="arrival-contest"
            />
          )}
        </Card>
      )}

      {helper ? (
        helper.body
      ) : view.kind === 'poster' ? (
        <PosterPanel
          view={view}
          busy={busy}
          money={(cents) => formatMoney(cents, locale)}
          onAnswerLate={(keep) => void answerLate({ variables: { ...vars, keep } }).catch(refresh)}
          onConfirmCame={(came) =>
            void confirmCame({ variables: { ...vars, came } }).catch(refresh)
          }
          onAnswerEarly={(confirm) =>
            void answerEarly({
              variables: { ...vars, confirm, locale: graphqlLocale(locale) },
            }).catch(refresh)
          }
        />
      ) : null}

      {errorCode && (
        <Text accessibilityRole="alert" style={[textStyle('body'), styles.error]}>
          {i18n.exists(`errors.${errorCode}`) ? t(`errors.${errorCode}`) : t('arrival.loadError')}
        </Text>
      )}
    </>,
    helper?.footer,
  );
}

function EtaChoices({
  legend,
  picked,
  onPick,
  disabled,
}: {
  legend: string;
  picked: number | null;
  onPick(minutes: number): void;
  disabled: boolean;
}) {
  const { t } = useTranslation();
  return (
    <View accessibilityRole="radiogroup" accessibilityLabel={legend} style={styles.etas}>
      <Text style={[textStyle('label'), styles.ink]}>{legend}</Text>
      <View style={styles.chips}>
        {ETA_CHOICES.map((minutes) => (
          <Pressable
            key={minutes}
            accessibilityRole="radio"
            accessibilityState={{ checked: picked === minutes, disabled }}
            disabled={disabled}
            onPress={() => onPick(minutes)}
            style={[styles.chip, picked === minutes && styles.chipPicked]}
            testID={`arrival-eta-${minutes}`}
          >
            <Text style={[textStyle('label'), picked === minutes ? styles.onGreen : styles.ink]}>
              {t('arrival.etaMinutes', { minutes })}
            </Text>
          </Pressable>
        ))}
      </View>
    </View>
  );
}

interface HelperState {
  eta: number | null;
  reportingLate: boolean;
  confirmingCantCome: boolean;
}

const HELPER_IDLE: HelperState = { eta: null, reportingLate: false, confirmingCantCome: false };

/**
 * The helper's taps (mockups 18a, 18g): the arrival times and the hints in the body, the main
 * action pinned in the footer. Running late and "I can't come" stay as quieter buttons.
 */
function helperActions({
  view,
  busy,
  state,
  setState,
  t,
  onMyWay,
  onLate,
  onArrived,
  onCantCome,
  onStartEarly,
}: {
  view: HelperArrival;
  busy: boolean;
  state: HelperState;
  setState(update: (state: HelperState) => HelperState): void;
  t: TFunction;
  onMyWay(etaMinutes: number | null): void;
  onLate(etaMinutes: number): void;
  onArrived(): void;
  onCantCome(): void;
  onStartEarly(): void;
}): { body: ReactNode; footer: ReactNode } {
  const { eta, reportingLate, confirmingCantCome } = state;
  const set = (patch: Partial<HelperState>) => setState((now) => ({ ...now, ...patch }));
  const choosing = view.canTap && (view.canMarkOnMyWay || reportingLate);

  const body = (
    <>
      {choosing && (
        <EtaChoices
          legend={reportingLate ? t('arrival.runningLate') : t('arrival.etaLegend')}
          picked={eta}
          onPick={(minutes) => set({ eta: minutes })}
          disabled={busy}
        />
      )}
      {view.canTap && view.canMarkOnMyWay && !reportingLate && (
        <Note>{t('arrival.onMyWayHint')}</Note>
      )}
      {view.canTap && confirmingCantCome && <Note tone="red">{t('arrival.cantComeBody')}</Note>}
      {view.canTap && !confirmingCantCome && (
        <View style={styles.quiet}>
          {!reportingLate && (
            <Button
              variant="secondary"
              size="sm"
              label={t('arrival.runningLate')}
              disabled={busy}
              onPress={() => set({ eta: null, reportingLate: true })}
              testID="arrival-running-late"
            />
          )}
          <Button
            variant="ghost"
            size="sm"
            label={t('arrival.cantCome')}
            disabled={busy}
            onPress={() => set({ confirmingCantCome: true })}
            testID="arrival-cant-come"
          />
          <Text style={[textStyle('small'), styles.muted]}>{t('arrival.noReplyHint')}</Text>
        </View>
      )}
      {view.canRequestEarlyStart && (
        <Card style={styles.card}>
          <Text style={[textStyle('small'), styles.muted]}>{t('arrival.startNowHint')}</Text>
          <Button
            variant="secondary"
            label={t('arrival.startNow')}
            disabled={busy}
            onPress={onStartEarly}
            testID="arrival-start-now"
          />
        </Card>
      )}
      {view.earlyStartPending && (
        <Text style={[textStyle('body'), styles.ink]}>{t('arrival.earlyStartWaiting')}</Text>
      )}
    </>
  );

  let footer: ReactNode = null;
  if (view.canTap && confirmingCantCome) {
    footer = (
      <>
        <Button
          variant="danger"
          label={t('arrival.cantComeConfirm')}
          disabled={busy}
          onPress={onCantCome}
          testID="arrival-cant-come-confirm"
        />
        <Button
          variant="ghost"
          size="sm"
          label={t('arrival.goBack')}
          disabled={busy}
          onPress={() => set({ confirmingCantCome: false })}
        />
      </>
    );
  } else if (view.canTap && reportingLate) {
    footer = (
      <>
        <Button
          label={t('arrival.sendLate')}
          disabled={busy || eta === null}
          onPress={() => eta !== null && onLate(eta)}
          testID="arrival-send-late"
        />
        <Button
          variant="ghost"
          size="sm"
          label={t('arrival.goBack')}
          disabled={busy}
          onPress={() => set({ eta: null, reportingLate: false })}
        />
        {/* A helper who reaches the door mid-report can still say so in one tap. */}
        <Button
          variant="ghost"
          size="sm"
          label={t('arrival.arrived')}
          disabled={busy}
          onPress={onArrived}
          testID="arrival-arrived"
        />
      </>
    );
  } else if (view.canTap) {
    const arrivedButton = (
      <Button
        variant={view.canMarkOnMyWay ? 'ghost' : 'primary'}
        size={view.canMarkOnMyWay ? 'sm' : 'md'}
        label={t('arrival.arrived')}
        disabled={busy}
        onPress={onArrived}
        testID="arrival-arrived"
      />
    );
    footer = view.canMarkOnMyWay ? (
      <>
        <Button
          label={t('arrival.onMyWay')}
          disabled={busy}
          onPress={() => onMyWay(eta)}
          testID="arrival-on-my-way"
        />
        {arrivedButton}
      </>
    ) : (
      arrivedButton
    );
  }
  return { body, footer };
}

function PosterPanel({
  view,
  busy,
  money,
  onAnswerLate,
  onConfirmCame,
  onAnswerEarly,
}: {
  view: PosterArrival;
  busy: boolean;
  money(cents: number): string;
  onAnswerLate(keep: boolean): void;
  onConfirmCame(came: boolean): void;
  onAnswerEarly(confirm: boolean): void;
}) {
  const { t } = useTranslation();
  const [sayingNo, setSayingNo] = useState(false);
  const offer = view.noShowOffer;
  return (
    <>
      {view.canAnswerLate && (
        <Card style={styles.card} testID="arrival-late">
          <Heading style={[textStyle('h2'), styles.ink]}>{t('arrival.lateTitle')}</Heading>
          <Text style={[textStyle('body'), styles.ink]}>{t('arrival.lateBody')}</Text>
          <Button
            label={t('arrival.lateKeep')}
            disabled={busy}
            onPress={() => onAnswerLate(true)}
            testID="arrival-late-keep"
          />
          <Button
            variant="secondary"
            label={t('arrival.lateCancel')}
            disabled={busy}
            onPress={() => onAnswerLate(false)}
            testID="arrival-late-cancel"
          />
        </Card>
      )}

      {view.canAnswerEarlyStart && (
        <Card style={styles.card} testID="arrival-early-start">
          <Heading style={[textStyle('h2'), styles.ink]}>{t('arrival.earlyStartTitle')}</Heading>
          <Text style={[textStyle('body'), styles.ink]} testID="arrival-early-body">
            {t(
              view.earlyStartWithdrawal
                ? 'arrival.earlyStartBodyWithdrawal'
                : 'arrival.earlyStartBody',
            )}
          </Text>
          <Button
            label={t('arrival.earlyStartConfirm')}
            disabled={busy}
            onPress={() => onAnswerEarly(true)}
            testID="arrival-early-confirm"
          />
          <Button
            variant="secondary"
            label={t('arrival.earlyStartDecline')}
            disabled={busy}
            onPress={() => onAnswerEarly(false)}
            testID="arrival-early-decline"
          />
        </Card>
      )}

      {(view.canConfirmCame || offer) && (
        <Card style={styles.card} testID="arrival-did-they-come">
          <Heading style={[textStyle('h2'), styles.ink]}>{t('arrival.cameTitle')}</Heading>
          <Text style={[textStyle('body'), styles.ink]}>{t('arrival.cameBody')}</Text>
          {view.canConfirmCame && (
            <Button
              label={t('arrival.cameYes')}
              disabled={busy}
              onPress={() => onConfirmCame(true)}
              testID="arrival-came-yes"
            />
          )}
          {offer &&
            (sayingNo ? (
              <View style={styles.card}>
                <Text style={[textStyle('body'), styles.ink]} testID="arrival-no-show-warning">
                  {t('arrival.noShowWarning', { amount: money(offer.refundCents) })}
                </Text>
                {offer.creditCents > 0 && (
                  <Text style={[textStyle('body'), styles.ink]}>
                    {t('arrival.noShowCredit', { amount: money(offer.creditCents) })}
                  </Text>
                )}
                <Button
                  variant="danger"
                  label={t('arrival.noShowConfirm')}
                  disabled={busy}
                  onPress={() => onConfirmCame(false)}
                  testID="arrival-no-show-confirm"
                />
                <Button
                  variant="ghost"
                  label={t('arrival.goBack')}
                  disabled={busy}
                  onPress={() => setSayingNo(false)}
                />
              </View>
            ) : (
              <Button
                variant="secondary"
                label={t('arrival.cameNo')}
                disabled={busy}
                onPress={() => setSayingNo(true)}
                testID="arrival-came-no"
              />
            ))}
        </Card>
      )}
    </>
  );
}

const styles = StyleSheet.create({
  ink: { color: colors.ink },
  muted: { color: colors.inkMuted },
  onGreen: { color: colors.white },
  error: { color: colors.error },
  card: { gap: spacing.sm },
  // The mockups' small capitals over the summary ("TODAY · 9:00").
  section: { textTransform: 'uppercase' },
  // A no-show report wants attention: the mockups' orange outline (18h).
  alert: { borderWidth: borderWidth.focus, borderColor: colors.orange },
  quiet: { gap: spacing.sm },
  etas: { gap: spacing.sm },
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
  chipPicked: { borderColor: colors.green, backgroundColor: colors.green },
});

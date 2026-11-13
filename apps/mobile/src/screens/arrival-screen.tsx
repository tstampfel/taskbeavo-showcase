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
import { colors, spacing } from '@taskbeavo/ui-tokens';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Button, Card } from '../components/ui';
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
  if (!current || (!viewer && viewerQuery.loading)) {
    return (
      <SafeAreaView style={styles.safeArea}>
        {booking.loading || viewerQuery.loading ? (
          <ActivityIndicator color={colors.green} />
        ) : (
          retry
        )}
      </SafeAreaView>
    );
  }
  const view = arrivalView(current, arrivalRole(current, viewer?.id ?? null));
  if (view.kind === 'unknownViewer') {
    // Never show one side's screen to someone whose side isn't known.
    return <SafeAreaView style={styles.safeArea}>{retry}</SafeAreaView>;
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

  return (
    <SafeAreaView style={styles.safeArea}>
      <ScrollView contentContainerStyle={styles.content}>
        <Text accessibilityRole="header" style={[textStyle('h1'), styles.title]}>
          {t('arrival.title')}
        </Text>

        <Text style={[textStyle('body'), styles.ink]} testID="arrival-status">
          {view.kind === 'helper'
            ? t(`arrival.statusHelper.${status.key}`, { time: statusTime })
            : t(`arrival.statusPoster.${status.key}`, { time: statusTime })}
        </Text>

        {view.noShowRejected && (
          <Text style={[textStyle('body'), styles.ink]}>{t('arrival.rejected')}</Text>
        )}
        {view.earlyStartConfirmed && (
          <Text style={[textStyle('body'), styles.ink]}>{t('arrival.earlyStartConfirmed')}</Text>
        )}

        {view.noShow && (
          <Card style={styles.card} testID="arrival-no-show">
            <Text style={[textStyle('body'), styles.ink]}>
              {view.noShow.settled
                ? t(view.kind === 'helper' ? 'arrival.settledHelper' : 'arrival.settledPoster')
                : view.noShow.contested
                  ? t(
                      view.kind === 'helper'
                        ? 'arrival.contestedHelper'
                        : 'arrival.contestedPoster',
                    )
                  : t(
                      view.kind === 'helper' ? 'arrival.reportedHelper' : 'arrival.reportedPoster',
                      {
                        time: time(view.noShow.contestBy),
                      },
                    )}
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

        {view.kind === 'helper' ? (
          <HelperPanel
            view={view}
            busy={busy}
            onMyWay={(etaMinutes) =>
              void onMyWay({ variables: { ...vars, etaMinutes } }).catch(refresh)
            }
            onLate={(etaMinutes) =>
              void late({ variables: { ...vars, etaMinutes } }).catch(refresh)
            }
            onArrived={() => void arrived({ variables: vars }).catch(refresh)}
            onCantCome={() =>
              void cantCome({ variables: { id: bookingId, reason: 'CANT_COME' } })
                .then(refresh)
                .catch(refresh)
            }
            onStartEarly={() => void startEarly({ variables: vars }).catch(refresh)}
          />
        ) : (
          <PosterPanel
            view={view}
            busy={busy}
            money={(cents) => formatMoney(cents, locale)}
            onAnswerLate={(keep) =>
              void answerLate({ variables: { ...vars, keep } }).catch(refresh)
            }
            onConfirmCame={(came) =>
              void confirmCame({ variables: { ...vars, came } }).catch(refresh)
            }
            onAnswerEarly={(confirm) =>
              void answerEarly({
                variables: { ...vars, confirm, locale: graphqlLocale(locale) },
              }).catch(refresh)
            }
          />
        )}

        {errorCode && (
          <Text accessibilityRole="alert" style={[textStyle('body'), styles.error]}>
            {i18n.exists(`errors.${errorCode}`) ? t(`errors.${errorCode}`) : t('arrival.loadError')}
          </Text>
        )}

        <Button variant="ghost" label={t('arrival.back')} onPress={onBack} />
      </ScrollView>
    </SafeAreaView>
  );
}

function EtaChoices({
  picked,
  onPick,
  disabled,
}: {
  picked: number | null;
  onPick(minutes: number): void;
  disabled: boolean;
}) {
  const { t } = useTranslation();
  return (
    <View accessibilityRole="radiogroup" accessibilityLabel={t('arrival.etaLegend')}>
      <Text style={[textStyle('small'), styles.muted]}>{t('arrival.etaLegend')}</Text>
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
            <Text style={[textStyle('body'), picked === minutes ? styles.onGreen : styles.ink]}>
              {t('arrival.etaMinutes', { minutes })}
            </Text>
          </Pressable>
        ))}
      </View>
    </View>
  );
}

function HelperPanel({
  view,
  busy,
  onMyWay,
  onLate,
  onArrived,
  onCantCome,
  onStartEarly,
}: {
  view: HelperArrival;
  busy: boolean;
  onMyWay(etaMinutes: number | null): void;
  onLate(etaMinutes: number): void;
  onArrived(): void;
  onCantCome(): void;
  onStartEarly(): void;
}) {
  const { t } = useTranslation();
  const [eta, setEta] = useState<number | null>(null);
  const [reportingLate, setReportingLate] = useState(false);
  const [confirmingCantCome, setConfirmingCantCome] = useState(false);
  return (
    <>
      {view.canTap && (
        <Card style={styles.card}>
          {(view.canMarkOnMyWay || reportingLate) && (
            <EtaChoices picked={eta} onPick={setEta} disabled={busy} />
          )}
          {view.canMarkOnMyWay && (
            <>
              <Text style={[textStyle('small'), styles.muted]}>{t('arrival.onMyWayHint')}</Text>
              <Button
                label={t('arrival.onMyWay')}
                disabled={busy}
                onPress={() => onMyWay(eta)}
                testID="arrival-on-my-way"
              />
            </>
          )}
          <Button
            variant="secondary"
            label={t('arrival.arrived')}
            disabled={busy}
            onPress={onArrived}
            testID="arrival-arrived"
          />
          {reportingLate ? (
            <Button
              variant="secondary"
              label={t('arrival.sendLate')}
              disabled={busy || eta === null}
              onPress={() => eta !== null && onLate(eta)}
              testID="arrival-send-late"
            />
          ) : (
            <Button
              variant="secondary"
              label={t('arrival.runningLate')}
              disabled={busy}
              onPress={() => {
                setEta(null);
                setReportingLate(true);
              }}
              testID="arrival-running-late"
            />
          )}
          {confirmingCantCome ? (
            <View style={styles.card}>
              <Text style={[textStyle('body'), styles.ink]}>{t('arrival.cantComeBody')}</Text>
              <Button
                variant="danger"
                label={t('arrival.cantComeConfirm')}
                disabled={busy}
                onPress={onCantCome}
                testID="arrival-cant-come-confirm"
              />
              <Button
                variant="ghost"
                label={t('arrival.goBack')}
                disabled={busy}
                onPress={() => setConfirmingCantCome(false)}
              />
            </View>
          ) : (
            <Button
              variant="ghost"
              label={t('arrival.cantCome')}
              disabled={busy}
              onPress={() => setConfirmingCantCome(true)}
              testID="arrival-cant-come"
            />
          )}
          <Text style={[textStyle('small'), styles.muted]}>{t('arrival.noReplyHint')}</Text>
        </Card>
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
          <Text accessibilityRole="header" style={[textStyle('h2'), styles.ink]}>
            {t('arrival.lateTitle')}
          </Text>
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
          <Text accessibilityRole="header" style={[textStyle('h2'), styles.ink]}>
            {t('arrival.earlyStartTitle')}
          </Text>
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
          <Text accessibilityRole="header" style={[textStyle('h2'), styles.ink]}>
            {t('arrival.cameTitle')}
          </Text>
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
  safeArea: { flex: 1, backgroundColor: colors.offWhite },
  content: { gap: spacing.lg, padding: spacing.xl },
  title: { color: colors.green },
  ink: { color: colors.ink },
  muted: { color: colors.inkMuted },
  onGreen: { color: colors.white },
  error: { color: colors.error },
  card: { gap: spacing.sm },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, marginTop: spacing.xs },
  chip: {
    minHeight: 44,
    minWidth: 64,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: spacing.md,
    borderRadius: 22,
    borderWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.white,
  },
  chipPicked: { borderColor: colors.green, backgroundColor: colors.green },
});

import { useApolloClient, useMutation, useQuery } from '@apollo/client/react';
import {
  AcceptOfferDocument,
  BookingDocument,
  CheckoutPolicyDocument,
  CheckoutSessionDocument,
  TaskDocument,
  TaskOffersDocument,
  type TaskOffersQuery,
  UNTICKED,
  apiErrorCode,
  canPay,
  checkoutConsentInput,
  checkoutView,
  consentTexts,
  graphqlLocale,
  secondsUntilExpiry,
  subscribeToBooking,
  type BookingFieldsFragment,
  type CheckoutPolicyQuery,
  type CheckoutView,
  type ConsentTicks,
  type Submitted,
} from '@taskbeavo/graphql';
import { defaultLocale, formatMoney, isLocale, type Locale } from '@taskbeavo/i18n';
import { colors, radii, spacing } from '@taskbeavo/ui-tokens';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import {
  AccessibilityInfo,
  ActivityIndicator,
  Platform,
  Pressable,
  StyleSheet,
  View,
} from 'react-native';
import { Text } from '../components/ui/text';
import { Checkbox } from '../components/sign-in/checkbox';
import { TraderIdentity } from '../components/trader-identity';
import { AmountRow, Badge, Button, Card, Heading, Screen, Sheet } from '../components/ui';
import { textStyle } from '../components/ui/text-style';
import { fonts } from '../fonts';
import type { PaymentSheet } from '../payments/stripe-config';

type Policy = NonNullable<CheckoutPolicyQuery['currentLegalDocument']>;
/** What the checkout showed for the offer: acceptOffer refuses it if any of it changed. */
type ShownOffer = Pick<
  TaskOffersQuery['taskOffers'][number],
  'priceCents' | 'totalCents' | 'serviceFeeScheduleVersion' | 'helperIsTrader'
>;

export interface CheckoutScreenProps {
  /** The task of the offer being checked out; a booking brings its own. */
  taskId?: string;
  /** Checkout of an offer not accepted yet: the summary and the two consent boxes first. */
  offerId?: string;
  /** Back to an open checkout, or its outcome (a push, the booking screen). */
  bookingId?: string;
  sheet: PaymentSheet;
  onBackToOffers(taskId: string): void;
  onViewBooking(bookingId: string): void;
  /** Opens a legal page (the full cancellation policy) in the in-app browser. */
  openLegal(page: 'cancellation', locale: Locale): void;
  /** Opens the price list (/fees, BEAV-191) in the in-app browser. */
  openFees(locale: Locale): void;
  now?: () => Date;
}

// While confirming, the booking is also refetched in case the live event is missed.
const CONFIRMING_POLL_MS = 5_000;
const COUNTDOWN_TICK_MS = 15_000;

/**
 * Checkout (mockups 6a–6f). The poster ticks both separate, unticked boxes, worded by the
 * CANCELLATION version in force (never the catalogues), and pays in Stripe's sheet. Nothing
 * here says "paid": after the sheet, "Confirming payment…" stays until the server reports
 * FUNDS_HELD (bookingUpdated, the poll, or the refetch on return to the foreground). A
 * declined card keeps the checkout open on the same PaymentIntent; leaving and coming back
 * uses checkoutSession until the checkout expires. The client secret lives in a ref only:
 * never in state that persists, never logged.
 */
export function CheckoutScreen({
  taskId,
  offerId,
  bookingId: initialBookingId,
  sheet,
  onBackToOffers,
  onViewBooking,
  openLegal,
  openFees,
  now = () => new Date(),
}: CheckoutScreenProps) {
  const { t, i18n } = useTranslation();
  const locale: Locale = isLocale(i18n.language) ? i18n.language : defaultLocale;
  const money = (cents: number) => formatMoney(cents, locale);
  const [bookingId, setBookingId] = useState(initialBookingId ?? null);
  const secret = useRef<string | null>(null);
  const [submitted, setSubmitted] = useState<Submitted | null>(null);
  // The last attempt ID while the sheet is open; a newer AUTHENTICATION_STARTED is 3D Secure.
  const [sheetOpen, setSheetOpen] = useState<Submitted | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const client = useApolloClient();

  const booking = useQuery(BookingDocument, {
    variables: { id: bookingId ?? '' },
    skip: !bookingId,
    fetchPolicy: 'cache-and-network',
  });
  const { subscribeToMore, startPolling, stopPolling } = booking;
  useEffect(
    () => (bookingId ? subscribeToBooking(subscribeToMore, bookingId) : undefined),
    [subscribeToMore, bookingId],
  );

  const current = booking.data?.booking;
  const attempt = current?.lastPaymentAttempt;
  // Attempts are told apart by ID, never by clock (review R1-2).
  const lastAttemptId = attempt?.id ?? null;
  const authenticating =
    sheetOpen !== null &&
    attempt?.kind === 'AUTHENTICATION_STARTED' &&
    attempt.id !== sheetOpen.lastAttemptId;
  const view: CheckoutView | null = current
    ? checkoutView(current, { submitted, authenticating }, now())
    : null;

  useEffect(() => {
    if (view?.kind !== 'confirming') return;
    startPolling(CONFIRMING_POLL_MS);
    return () => stopPolling();
  }, [view?.kind, startPolling, stopPolling]);

  const [, setTick] = useState(0);
  useEffect(() => {
    if (view?.kind !== 'pay') return;
    const timer = setInterval(() => setTick((tick) => tick + 1), COUNTDOWN_TICK_MS);
    return () => clearInterval(timer);
  }, [view?.kind]);

  useEffect(() => {
    if (view?.kind === 'held')
      AccessibilityInfo.announceForAccessibility(t('checkout.held.notified'));
    if (view?.kind === 'pay' && view.decline) {
      AccessibilityInfo.announceForAccessibility(t('checkout.declined.title'));
    }
  }, [view?.kind, view && view.kind === 'pay' ? view.decline : null, t]);

  const [acceptOffer] = useMutation(AcceptOfferDocument, { fetchPolicy: 'no-cache' });

  /**
   * `seen` is the last attempt ID the screen had when the sheet opened. The sheet's button
   * confirms the card payment of the booking already made, never a second order button
   * (review R3-1), so it carries checkout.cardPay instead of Stripe's "Pay €X".
   */
  async function present(clientSecret: string, seen: string | null, totalCents: number) {
    setSheetOpen({ lastAttemptId: seen });
    const result = await sheet.pay(
      clientSecret,
      t('checkout.cardPay', { amount: money(totalCents) }),
    );
    setSheetOpen(null);
    // A 3D Secure start while the sheet was open isn't a decline, so `seen` stays the marker.
    if (result === 'paid') setSubmitted({ lastAttemptId: seen });
    if (result === 'failed') setError('checkout.decline.generic');
  }

  /** Pay again on the open checkout: the same PaymentIntent, its secret from the server. */
  async function payAgain(id: string, totalCents: number) {
    setBusy(true);
    setError(null);
    try {
      if (!secret.current) {
        const { data } = await client.query({
          query: CheckoutSessionDocument,
          variables: { bookingId: id },
          fetchPolicy: 'no-cache',
        });
        if (!data) throw new Error('No checkout session');
        if (data.checkoutSession.paymentPending) {
          setSubmitted({ lastAttemptId });
          return;
        }
        secret.current = data.checkoutSession.clientSecret;
      }
      await present(secret.current, lastAttemptId, totalCents);
    } catch (caught) {
      secret.current = null;
      setError(`errors.${apiErrorCode(caught) ?? 'PAYMENT_UNAVAILABLE'}`);
      void booking.refetch().catch(() => undefined);
    } finally {
      setBusy(false);
    }
  }

  async function accept(policy: Policy, ticks: ConsentTicks, shown: ShownOffer, offer: string) {
    setBusy(true);
    setError(null);
    try {
      const { data } = await acceptOffer({
        variables: {
          offerId: offer,
          expectedPriceCents: shown.priceCents,
          expectedTotalCents: shown.totalCents,
          expectedFeeScheduleVersion: shown.serviceFeeScheduleVersion,
          consent: checkoutConsentInput(
            policy,
            ticks,
            Platform.OS === 'ios' ? 'IOS' : 'ANDROID',
            shown.helperIsTrader,
          ),
        },
      });
      if (!data) throw new Error('No checkout session');
      const session = data.acceptOffer;
      secret.current = session.clientSecret;
      client.writeQuery({
        query: BookingDocument,
        variables: { id: session.booking.id },
        data: { booking: session.booking },
      });
      setBookingId(session.booking.id);
      await present(
        session.clientSecret,
        session.booking.lastPaymentAttempt?.id ?? null,
        session.booking.totalCents,
      );
    } catch (caught) {
      setError(`errors.${apiErrorCode(caught) ?? 'PAYMENT_UNAVAILABLE'}`);
    } finally {
      setBusy(false);
    }
  }

  let body: ReactNode;
  if (!bookingId) {
    body = (
      <OfferCheckout
        taskId={taskId ?? ''}
        offerId={offerId ?? ''}
        locale={locale}
        busy={busy}
        error={error}
        openLegal={openLegal}
        openFees={openFees}
        onPay={(policy, ticks, shown) => void accept(policy, ticks, shown, offerId ?? '')}
      />
    );
  } else if (!current || !view) {
    body = booking.error ? (
      <Failure onRetry={() => void booking.refetch().catch(() => undefined)} />
    ) : (
      <Loading />
    );
  } else {
    body = (
      <BookingCheckout
        booking={current}
        view={view}
        money={money}
        busy={busy}
        error={error}
        expiresInSeconds={secondsUntilExpiry(current, now())}
        onPay={() => void payAgain(current.id, current.totalCents)}
        onBackToOffers={() => onBackToOffers(current.taskId)}
        onViewBooking={() => onViewBooking(current.id)}
      />
    );
  }

  // Back goes to the offers before the booking exists, then to the booking itself.
  const back = bookingId
    ? { onBack: () => onViewBooking(bookingId), backLabel: t('checkout.backToBooking') }
    : taskId
      ? { onBack: () => onBackToOffers(taskId), backLabel: t('checkout.back') }
      : {};
  return (
    <Screen title={t('checkout.title')} testID="checkout-screen" {...back}>
      {body}
    </Screen>
  );
}

function Loading() {
  const { t } = useTranslation();
  return <ActivityIndicator accessibilityLabel={t('checkout.title')} testID="checkout-loading" />;
}

function Failure({ onRetry, message }: { onRetry(): void; message?: string }) {
  const { t } = useTranslation();
  return (
    <View style={styles.stack} testID="checkout-error">
      <Text accessibilityRole="alert" style={[textStyle('body'), styles.ink]}>
        {message ?? t('checkout.loadError')}
      </Text>
      <Button label={t('checkout.retry')} onPress={onRetry} />
    </View>
  );
}

function Summary({
  price,
  fee,
  credit,
  total,
  money,
  children,
}: {
  /** What is being booked, above the lines (mockup 6a). */
  children?: ReactNode;
  price: number;
  fee: number;
  /** The poster's TaskBeavo credit taken off the fee (BEAV-123): its own line. */
  credit: number;
  total: number;
  money(cents: number): string;
}) {
  const { t } = useTranslation();
  return (
    <Card testID="checkout-summary" style={styles.summary}>
      {children}
      <Line label={t('checkout.summary.price')} value={money(price)} />
      <Line label={t('checkout.summary.fee')} value={money(fee)} />
      {credit > 0 ? (
        <Line label={t('checkout.summary.credit')} value={`−${money(credit)}`} />
      ) : null}
      <View style={styles.rule} />
      <Line label={t('checkout.summary.total')} value={money(total)} strong />
    </Card>
  );
}

function Line({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
  return (
    <AmountRow
      label={label}
      value={value}
      style={[textStyle(strong ? 'label' : 'body'), styles.ink]}
    />
  );
}

function ErrorNote({ messageKey }: { messageKey: string | null }) {
  const { t } = useTranslation();
  if (!messageKey) return null;
  return (
    <Text
      accessibilityRole="alert"
      style={[textStyle('body'), styles.error]}
      testID="checkout-alert"
    >
      {t(messageKey)}
    </Text>
  );
}

function OfferCheckout({
  taskId,
  offerId,
  locale,
  busy,
  error,
  onPay,
  openLegal,
  openFees,
}: {
  taskId: string;
  offerId: string;
  locale: Locale;
  busy: boolean;
  error: string | null;
  openLegal: CheckoutScreenProps['openLegal'];
  openFees: CheckoutScreenProps['openFees'];
  onPay(policy: Policy, ticks: ConsentTicks, shown: ShownOffer): void;
}) {
  const { t } = useTranslation();
  const [ticks, setTicks] = useState<ConsentTicks>(UNTICKED);
  const [reading, setReading] = useState(false);
  const offers = useQuery(TaskOffersDocument, { variables: { taskId } });
  const { refetch: refetchOffers } = offers;
  useEffect(() => {
    if (error === 'errors.OFFER_CHANGED' || error === 'errors.FEE_CHANGED') {
      // The price or the fee schedule changed: reload the offer to show the new total.
      void refetchOffers().catch(() => undefined);
      return;
    }
    if (error !== 'errors.CONSENT_WORDING_CHANGED') return;
    // The helper's status changed: reload it and ask for both boxes in the new wording.
    setTicks(UNTICKED);
    void refetchOffers().catch(() => undefined);
  }, [error, refetchOffers]);
  const task = useQuery(TaskDocument, { variables: { id: taskId } }).data?.task;
  const policyQuery = useQuery(CheckoutPolicyDocument, {
    variables: { language: graphqlLocale(locale) },
    fetchPolicy: 'network-only',
  });
  const offer = offers.data?.taskOffers.find((item) => item.id === offerId);
  const policy = policyQuery.data?.currentLegalDocument ?? null;
  const money = (cents: number) => formatMoney(cents, locale);

  if (offers.error || policyQuery.error) {
    return (
      <Failure
        onRetry={() => {
          void offers.refetch().catch(() => undefined);
          void policyQuery.refetch().catch(() => undefined);
        }}
      />
    );
  }
  if (!offers.data || policyQuery.loading) return <Loading />;
  if (!offer)
    return <Failure message={t('errors.OFFER_NOT_FOUND')} onRetry={() => void offers.refetch()} />;

  // The lawyer's wording for this helper type (user decision 2026-11-04, review R1-1).
  const consent = consentTexts(policy, offer.helperIsTrader);
  return (
    <>
      <Summary
        price={offer.priceCents}
        fee={offer.serviceFeeCents}
        credit={offer.serviceFeeCreditCents}
        total={offer.totalCents}
        money={money}
      >
        {task ? <Text style={[textStyle('label'), styles.ink]}>{task.title}</Text> : null}
        <Badge
          label={t(offer.helperIsTrader ? 'offers.trader.business' : 'offers.trader.private')}
          tone={offer.helperIsTrader ? 'green' : 'grey'}
        />
        <TraderIdentity offer={offer} />
        <View style={styles.rule} />
      </Summary>
      <Pressable
        accessibilityRole="link"
        onPress={() => openFees(locale)}
        style={styles.link}
        testID="checkout-fees"
      >
        <Text style={[textStyle('small'), styles.linkText]}>{t('checkout.feesLink')}</Text>
      </Pressable>
      <Text style={[textStyle('label'), styles.green]}>{t('checkout.secure')}</Text>
      <HowItWorks />
      <Text style={[textStyle('small'), styles.muted]}>
        {t(offer.helperIsTrader ? 'checkout.helper.business' : 'checkout.helper.private')}
      </Text>
      {policy && consent ? (
        <View accessibilityRole="none" style={styles.stack} testID="checkout-consent">
          <Heading style={[textStyle('label'), styles.ink]}>{t('checkout.consent.legend')}</Heading>
          <Checkbox
            checked={ticks.cancellationPolicy}
            onChange={(checked) => setTicks((now) => ({ ...now, cancellationPolicy: checked }))}
            label={consent.cancellationPolicy}
            testID="consent-cancellation-policy"
          />
          <Checkbox
            checked={ticks.earlyStart}
            onChange={(checked) => setTicks((now) => ({ ...now, earlyStart: checked }))}
            label={consent.earlyStart}
            testID="consent-early-start"
          />
          <Pressable
            accessibilityRole="link"
            onPress={() => setReading(true)}
            style={styles.link}
            testID="policy-open"
          >
            <Text style={[textStyle('small'), styles.linkText]}>{t('checkout.policy.open')}</Text>
          </Pressable>
          <Pressable
            accessibilityRole="link"
            onPress={() => openLegal('cancellation', locale)}
            style={styles.link}
            testID="policy-page"
          >
            <Text style={[textStyle('small'), styles.linkText]}>
              {t('checkout.policy.fullPage')}
            </Text>
          </Pressable>
          <Sheet
            visible={reading}
            onClose={() => setReading(false)}
            title={t('checkout.policy.open')}
            closeLabel={t('checkout.policy.close')}
            testID="policy-sheet"
          >
            <Text style={[textStyle('small'), styles.muted]}>
              {t('checkout.policy.version', { version: policy.version })}
            </Text>
            <Text style={[textStyle('body'), styles.ink]}>{policy.text}</Text>
          </Sheet>
        </View>
      ) : (
        <Text
          accessibilityRole="alert"
          style={[textStyle('body'), styles.error]}
          testID="policy-unavailable"
        >
          {t('checkout.policy.unavailable')}
        </Text>
      )}
      <ErrorNote messageKey={error} />
      {/* The fee notice and the amount due sit directly above the one order button, on the
          same screen as the summary and the tick boxes; plain text, never collapsed (user
          decision after the fee-consent opinions; DRAFT wording, REVIEW.md). */}
      <View style={styles.feeNotice} testID="checkout-fee-notice">
        <Text style={[textStyle('small'), styles.ink]}>
          {t('checkout.feeNotice', {
            price: money(offer.priceCents),
            fee: money(offer.serviceFeeCents),
            percent: offer.serviceFeePercent,
            minimum: money(offer.serviceFeeMinimumCents),
            total: money(offer.totalCents + offer.serviceFeeCreditCents),
          })}
        </Text>
        {offer.serviceFeeCreditCents > 0 ? (
          <Text style={[textStyle('small'), styles.ink]} testID="checkout-credit-notice">
            {t('checkout.creditNotice', { credit: money(offer.serviceFeeCreditCents) })}
          </Text>
        ) : null}
        <Text style={[textStyle('label'), styles.ink]} testID="checkout-total-due">
          {t('checkout.totalDue', { total: money(offer.totalCents) })}
        </Text>
      </View>
      <Button
        label={t('checkout.pay', { amount: money(offer.totalCents) })}
        disabled={!policy || !canPay(policy, ticks)}
        busy={busy}
        onPress={() => policy && onPay(policy, ticks, offer)}
        testID="checkout-pay"
      />
    </>
  );
}

function HowItWorks() {
  const { t } = useTranslation();
  return (
    <Card variant="flat">
      <Text style={[textStyle('label'), styles.ink]}>{t('checkout.how.title')}</Text>
      <Text style={[textStyle('small'), styles.ink]}>{t('checkout.how.held')}</Text>
      <Text style={[textStyle('small'), styles.ink]}>{t('checkout.how.request')}</Text>
      <Text style={[textStyle('small'), styles.ink]}>{t('checkout.how.release')}</Text>
    </Card>
  );
}

function BookingCheckout({
  booking,
  view,
  money,
  busy,
  error,
  expiresInSeconds,
  onPay,
  onBackToOffers,
  onViewBooking,
}: {
  booking: BookingFieldsFragment;
  view: CheckoutView;
  money(cents: number): string;
  busy: boolean;
  error: string | null;
  expiresInSeconds: number | null;
  onPay(): void;
  onBackToOffers(): void;
  onViewBooking(): void;
}) {
  const { t } = useTranslation();
  const total = money(booking.totalCents);
  switch (view.kind) {
    case 'pay':
      return (
        <>
          {view.decline ? (
            <Card style={styles.declined} testID="checkout-declined">
              <Text accessibilityRole="alert" style={[textStyle('label'), styles.error]}>
                {t('checkout.declined.title')}
              </Text>
              <Text style={[textStyle('body'), styles.ink]}>
                {t(`checkout.decline.${view.decline}`)} {t('checkout.declined.nothingCharged')}
              </Text>
            </Card>
          ) : null}
          <Summary
            price={booking.amountCents}
            // The booking's fee already has the credit off; the summary shows the schedule's fee
            // and the credit as its own line, so add it back (R1-3).
            fee={booking.feeCents + (booking.creditCents ?? 0)}
            credit={booking.creditCents ?? 0}
            total={booking.totalCents}
            money={money}
          />
          {expiresInSeconds !== null ? (
            <Text style={[textStyle('small'), styles.muted]} testID="checkout-expiry">
              {t('checkout.expiresIn', { minutes: Math.max(1, Math.ceil(expiresInSeconds / 60)) })}
            </Text>
          ) : null}
          <ErrorNote messageKey={error} />
          <Button
            label={
              // Not the order button (that is the offer checkout's): it reopens the card
              // payment of the booking already made.
              view.decline
                ? t('checkout.declined.tryAgain')
                : t('checkout.cardPay', { amount: total })
            }
            busy={busy}
            onPress={onPay}
            testID="checkout-pay"
          />
        </>
      );
    case 'authenticating':
      return (
        <Outcome
          testID="checkout-authenticating"
          title={t('checkout.authenticating.title')}
          body={t('checkout.authenticating.body', { amount: total })}
          note={t('checkout.authenticating.note')}
          waiting={t('checkout.authenticating.waiting')}
        />
      );
    case 'confirming':
      return (
        <Outcome
          testID="checkout-confirming"
          title={t('checkout.confirming.title')}
          body={t('checkout.confirming.body')}
          waiting={t('checkout.confirming.title')}
        />
      );
    case 'held':
      return (
        <Outcome
          testID="checkout-held"
          done
          title={t('checkout.held.title', { amount: total })}
          body={t('checkout.held.notified')}
          note={`${t('checkout.held.release')}\n\n${t('checkout.held.email')}`}
        >
          <Button label={t('checkout.held.viewBooking')} onPress={onViewBooking} />
        </Outcome>
      );
    case 'withdrawn':
      return (
        <Outcome
          testID="checkout-withdrawn"
          title={t('checkout.withdrawn.title')}
          body={t('checkout.withdrawn.body')}
        >
          <Button
            label={t('checkout.withdrawn.others')}
            variant="secondary"
            onPress={onBackToOffers}
          />
        </Outcome>
      );
    case 'notStarted':
      return (
        <Outcome
          testID="checkout-not-started"
          title={t('checkout.notStarted.title')}
          body={t('checkout.notStarted.body')}
        >
          <Button label={t('checkout.back')} variant="secondary" onPress={onBackToOffers} />
        </Outcome>
      );
    case 'expired':
      return (
        <Outcome
          testID="checkout-expired"
          title={t('checkout.expired.title')}
          body={t('checkout.expired.body')}
        >
          <Button label={t('checkout.back')} variant="secondary" onPress={onBackToOffers} />
        </Outcome>
      );
  }
}

function Outcome({
  title,
  body,
  note,
  waiting,
  done,
  children,
  testID,
}: {
  /** The payment is held: the mockup's green tick (6c). */
  done?: boolean;
  title: string;
  body: string;
  note?: string;
  waiting?: string;
  children?: ReactNode;
  testID: string;
}) {
  return (
    <View style={[styles.stack, styles.outcome]} testID={testID}>
      {done ? (
        <View style={styles.tick} importantForAccessibility="no" accessibilityElementsHidden>
          <Text style={styles.tickMark} allowFontScaling={false}>
            ✓
          </Text>
        </View>
      ) : null}
      <Heading style={[textStyle('h1'), styles.ink, styles.centered]}>{title}</Heading>
      <Text style={[textStyle('body'), styles.ink, styles.centered]}>{body}</Text>
      {waiting ? <ActivityIndicator accessibilityLabel={waiting} color={colors.green} /> : null}
      {note ? (
        <Text style={[textStyle('small'), styles.muted, styles.centered]}>{note}</Text>
      ) : null}
      <View style={styles.actions}>{children}</View>
    </View>
  );
}

const styles = StyleSheet.create({
  stack: { gap: spacing.md },
  summary: { gap: spacing.sm },
  // Outcomes (6c–6f) sit in the middle of the screen, like the mockups' centred frames.
  outcome: { flexGrow: 1, justifyContent: 'center', alignItems: 'center' },
  centered: { textAlign: 'center' },
  actions: { alignSelf: 'stretch', gap: spacing.sm },
  tick: {
    width: 96,
    height: 96,
    borderRadius: radii.pill,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.green,
  },
  tickMark: { fontFamily: fonts.bold, fontSize: 48, lineHeight: 56, color: colors.white },
  ink: { color: colors.ink },
  muted: { color: colors.inkMuted },
  green: { color: colors.green },
  error: { color: colors.error },
  grow: { flex: 1 },
  rule: { height: 1, backgroundColor: colors.line },
  declined: { borderColor: colors.error, borderWidth: 2 },
  link: { minHeight: 44, justifyContent: 'center' },
  feeNotice: { gap: spacing.xs },
  linkText: { color: colors.green, textDecorationLine: 'underline' },
});

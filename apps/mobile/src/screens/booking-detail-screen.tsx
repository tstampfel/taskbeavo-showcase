import { NetworkStatus } from '@apollo/client';
import { useQuery } from '@apollo/client/react';
import {
  BookingDetailDocument,
  ViewerDocument,
  bookingDetailView,
  bookingRole,
  subscribeToBookingDetail,
  type BookingDetail,
  type BookingLinks,
  type BookingPlace,
  type DetailCheckout,
} from '@taskbeavo/graphql';
import { defaultLocale, formatDateTime, formatMoney, isLocale, type Locale } from '@taskbeavo/i18n';
import { colors, minTapTarget, spacing } from '@taskbeavo/ui-tokens';
import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { ActivityIndicator, Linking, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { TraderIdentity } from '../components/trader-identity';
import { Badge, Button, Card } from '../components/ui';
import { textStyle } from '../components/ui/text-style';

/** The booking screens the detail screen links into, by their route segment. */
export type BookingScreen = 'checkout' | 'arrival' | 'cancel' | 'no-access' | 'release';

export interface BookingDetailScreenProps {
  bookingId: string;
  onOpen(screen: BookingScreen): void;
}

const LINKS: readonly { link: keyof BookingLinks; screen: BookingScreen; primary?: boolean }[] = [
  { link: 'release', screen: 'release', primary: true },
  { link: 'noAccess', screen: 'no-access', primary: true },
  { link: 'arrival', screen: 'arrival' },
  { link: 'cancel', screen: 'cancel' },
];

/**
 * The booking detail screen for both sides (BEAV-115, mockup 05): where the booking stands,
 * the place, who the poster contracts with, and the way into the screens that act. Live
 * through bookingUpdated; the app refetches it on return to the foreground and when the
 * socket reconnects (src/apollo). The exact address and access details show only when the
 * server sends them (hard rule e); a live event that ends the booking takes them away.
 */
export function BookingDetailScreen({ bookingId, onOpen }: BookingDetailScreenProps) {
  const { t, i18n } = useTranslation();
  const locale: Locale = isLocale(i18n.language) ? i18n.language : defaultLocale;
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
  const current = booking.data?.booking;
  const viewer = viewerQuery.data?.viewer;

  const retry = (
    <View style={styles.content}>
      <Text accessibilityRole="alert" style={[textStyle('body'), styles.error]}>
        {t('bookingDetail.loadError')}
      </Text>
      <Button
        variant="secondary"
        label={t('bookingDetail.retry')}
        onPress={() => {
          void booking.refetch().catch(() => undefined);
          void viewerQuery.refetch().catch(() => undefined);
        }}
      />
    </View>
  );
  if (!current || (!viewer && viewerQuery.loading)) {
    return (
      <SafeAreaView style={styles.safeArea}>
        {booking.loading || viewerQuery.loading ? (
          <ActivityIndicator
            color={colors.green}
            accessibilityLabel={t('bookingDetail.metaTitle')}
          />
        ) : (
          retry
        )}
      </SafeAreaView>
    );
  }
  const view = bookingDetailView(current, bookingRole(current, viewer?.id ?? null), new Date(), {
    // A cached copy from an earlier screen may predate the end of the booking (hard rule e).
    fresh: booking.networkStatus !== NetworkStatus.loading,
  });
  // Never show one side's screen to someone whose side isn't known.
  if (view.kind === 'unknownViewer')
    return <SafeAreaView style={styles.safeArea}>{retry}</SafeAreaView>;

  const time = (at: unknown) => (typeof at === 'string' ? formatDateTime(at, locale, 'short') : '');
  const money = (cents: number) => formatMoney(cents, locale);
  const poster = view.role === 'poster';

  return (
    <SafeAreaView style={styles.safeArea}>
      <ScrollView contentContainerStyle={styles.content}>
        <Text accessibilityRole="header" style={[textStyle('h1'), styles.title]}>
          {current.task.title}
        </Text>
        <Badge
          label={t(`bookingDetail.status.${view.status}`)}
          tone={view.status === 'PAYMENT_REQUESTED' ? 'orange' : 'green'}
          testID="booking-status"
        />
        {view.steps && <Steps steps={view.steps} />}

        <Card style={styles.card}>
          <Text style={[textStyle('body'), styles.ink]}>
            {t('bookingDetail.startsAt', { time: time(current.slotStart) })}
          </Text>
          <Text style={[textStyle('body'), styles.ink]} testID="booking-amount">
            {poster
              ? t('bookingDetail.totalPoster', { total: money(current.totalCents) })
              : t('bookingDetail.amountHelper', { amount: money(current.amountCents) })}
          </Text>
          {view.steps && view.status !== 'AWAITING_PAYMENT' && view.status !== 'CLOSED' && (
            <Text style={[textStyle('small'), styles.muted]}>
              {poster
                ? t('bookingDetail.heldPoster', { total: money(current.totalCents) })
                : t('bookingDetail.heldHelper', { amount: money(current.amountCents) })}
            </Text>
          )}
          {view.confirmationEmailed && (
            <Text style={[textStyle('small'), styles.muted]}>{t('checkout.held.email')}</Text>
          )}
        </Card>

        {view.checkout && (
          <CheckoutNotice
            checkout={view.checkout}
            time={time}
            onPay={view.links.checkout ? () => onOpen('checkout') : undefined}
          />
        )}

        <PlaceCard place={view.place} />

        {view.trader && <HelperCard trader={view.trader} />}

        <Links view={view} onOpen={onOpen} />

        <Text style={[textStyle('small'), styles.muted]}>{t('bookingDetail.safety')}</Text>
      </ScrollView>
    </SafeAreaView>
  );
}

function Steps({ steps }: { steps: NonNullable<BookingDetail['steps']> }) {
  const { t } = useTranslation();
  return (
    <View
      accessibilityLabel={t('bookingDetail.stepsLabel')}
      style={styles.steps}
      testID="booking-steps"
    >
      {steps.map(({ step, state }) => {
        const label = t(`bookingDetail.steps.${step}`);
        return (
          <View
            key={step}
            accessible
            accessibilityLabel={
              state === 'done'
                ? t('bookingDetail.stepDone', { step: label })
                : state === 'current'
                  ? t('bookingDetail.stepCurrent', { step: label })
                  : label
            }
            style={styles.step}
            testID={`booking-step-${step}-${state}`}
          >
            <View style={[styles.dot, state !== 'todo' && styles.dotReached]} />
            <Text style={[textStyle('caption'), state === 'current' ? styles.ink : styles.muted]}>
              {label}
            </Text>
          </View>
        );
      })}
    </View>
  );
}

function CheckoutNotice({
  checkout,
  time,
  onPay,
}: {
  checkout: DetailCheckout;
  time(at: unknown): string;
  onPay?: () => void;
}) {
  const { t } = useTranslation();
  const lines =
    checkout.kind === 'pay'
      ? [
          checkout.decline ? t(`checkout.decline.${checkout.decline}`) : null,
          checkout.expiresAt
            ? t('bookingDetail.checkout.pay', { time: time(checkout.expiresAt) })
            : null,
        ]
      : checkout.kind === 'failed'
        ? [t(`bookingDetail.checkout.failed.${checkout.reason}`)]
        : [t(`bookingDetail.checkout.${checkout.kind}`)];
  return (
    <Card style={styles.card} testID="booking-checkout">
      {lines.map(
        (line) =>
          line && (
            <Text key={line} style={[textStyle('body'), styles.ink]}>
              {line}
            </Text>
          ),
      )}
      {onPay && (
        <Button
          label={t('bookingDetail.checkout.goToPayment')}
          onPress={onPay}
          testID="booking-pay"
        />
      )}
    </Card>
  );
}

function PlaceCard({ place }: { place: BookingPlace }) {
  const { t } = useTranslation();
  return (
    <Card style={styles.card} testID="booking-place">
      <Text accessibilityRole="header" style={[textStyle('h2'), styles.ink]}>
        {t('bookingDetail.place.title')}
      </Text>
      {place.kind === 'exact' ? (
        <>
          <Text style={[textStyle('body'), styles.ink]} testID="booking-address">
            {place.address}
          </Text>
          {place.onlyWhileBooked && (
            <Text style={[textStyle('small'), styles.muted]}>
              {t('bookingDetail.place.whileBooked')}
            </Text>
          )}
          {place.accessDetails ? (
            <Text style={[textStyle('body'), styles.ink]} testID="booking-access">
              {t('bookingDetail.place.access', { details: place.accessDetails })}
            </Text>
          ) : null}
          {place.contact && (
            <>
              <Text style={[textStyle('body'), styles.ink]}>
                {t('bookingDetail.place.contact', {
                  name: place.contact.name,
                  area: place.contact.area,
                })}
              </Text>
              {place.contact.phone && (
                <Button
                  variant="secondary"
                  size="sm"
                  label={t('bookingDetail.place.phone', { phone: place.contact.phone })}
                  onPress={() => void Linking.openURL(`tel:${place.contact?.phone ?? ''}`)}
                />
              )}
            </>
          )}
        </>
      ) : (
        <>
          <Text style={[textStyle('body'), styles.ink]} testID="booking-area">
            {place.area
              ? t('bookingDetail.place.area', { area: place.area })
              : t('bookingDetail.place.areaUnknown')}
          </Text>
          {place.hint && (
            <Text style={[textStyle('small'), styles.muted]}>
              {t(`bookingDetail.place.${place.hint}`)}
            </Text>
          )}
        </>
      )}
    </Card>
  );
}

function HelperCard({ trader }: { trader: NonNullable<BookingDetail['trader']> }) {
  const { t } = useTranslation();
  return (
    <Card style={styles.card} testID="booking-helper">
      <Text accessibilityRole="header" style={[textStyle('h2'), styles.ink]}>
        {t('bookingDetail.helperTitle')}
      </Text>
      {trader.helperIsTrader ? (
        <TraderIdentity offer={trader} />
      ) : (
        <Text style={[textStyle('body'), styles.ink]}>{t('offers.trader.private')}</Text>
      )}
    </Card>
  );
}

function Links({ view, onOpen }: { view: BookingDetail; onOpen(screen: BookingScreen): void }) {
  const { t } = useTranslation();
  const shown = LINKS.filter(({ link }) => view.links[link]);
  if (shown.length === 0) return null;
  return (
    <View style={styles.card}>
      <Text accessibilityRole="header" style={[textStyle('h2'), styles.ink]}>
        {t('bookingDetail.links.title')}
      </Text>
      {shown.map(({ link, screen, primary }) => (
        <Button
          key={link}
          variant={primary ? 'primary' : 'secondary'}
          label={t(`bookingDetail.links.${link}`)}
          onPress={() => onOpen(screen)}
          testID={`booking-link-${screen}`}
        />
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  safeArea: { flex: 1, backgroundColor: colors.offWhite },
  content: { gap: spacing.lg, padding: spacing.xl },
  title: { color: colors.green },
  ink: { color: colors.ink },
  muted: { color: colors.inkMuted },
  error: { color: colors.error },
  card: { gap: spacing.sm },
  steps: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.md },
  step: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
    minHeight: minTapTarget / 2,
  },
  dot: {
    width: spacing.sm,
    height: spacing.sm,
    borderRadius: spacing.sm / 2,
    backgroundColor: colors.line,
  },
  dotReached: { backgroundColor: colors.green },
});

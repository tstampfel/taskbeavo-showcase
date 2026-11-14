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
import { colors, radii, spacing } from '@taskbeavo/ui-tokens';
import { useEffect, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ActivityIndicator,
  Linking,
  StyleSheet,
  Text,
  View,
  useWindowDimensions,
} from 'react-native';
import { TraderIdentity } from '../components/trader-identity';
import { Badge, Button, Card, Heading, Note, Screen } from '../components/ui';
import { textStyle } from '../components/ui/text-style';

/** The booking screens the detail screen links into, by their route segment. */
export type BookingScreen = 'checkout' | 'arrival' | 'cancel' | 'no-access' | 'release';

export interface BookingDetailScreenProps {
  bookingId: string;
  onOpen(screen: BookingScreen): void;
  /** The top bar's back button. */
  onBack?: () => void;
}

type Link = { link: keyof BookingLinks; screen: BookingScreen; primary?: boolean };

/** From this text scale on, the progress steps are a list rather than four columns. */
const STACKED_STEPS_FONT_SCALE = 1.3;

const LINKS: readonly Link[] = [
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
export function BookingDetailScreen({ bookingId, onOpen, onBack }: BookingDetailScreenProps) {
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
  const placeholder = (children: ReactNode) => (
    <Screen
      title={t('bookingDetail.metaTitle')}
      onBack={onBack}
      backLabel={t('bookingDetail.back')}
    >
      {children}
    </Screen>
  );
  if (!current || (!viewer && viewerQuery.loading)) {
    return placeholder(
      booking.loading || viewerQuery.loading ? (
        <ActivityIndicator color={colors.green} accessibilityLabel={t('bookingDetail.metaTitle')} />
      ) : (
        retry
      ),
    );
  }
  const view = bookingDetailView(current, bookingRole(current, viewer?.id ?? null), new Date(), {
    // A cached copy from an earlier screen may predate the end of the booking (hard rule e).
    fresh: booking.networkStatus !== NetworkStatus.loading,
  });
  // Never show one side's screen to someone whose side isn't known.
  if (view.kind === 'unknownViewer') return placeholder(retry);

  const time = (at: unknown) => (typeof at === 'string' ? formatDateTime(at, locale, 'short') : '');
  const money = (cents: number) => formatMoney(cents, locale);
  const poster = view.role === 'poster';
  const held = view.steps && view.status !== 'AWAITING_PAYMENT' && view.status !== 'CLOSED';
  const links = LINKS.filter(({ link }) => view.links[link]);

  return (
    <Screen
      title={current.task.title}
      onBack={onBack}
      backLabel={t('bookingDetail.back')}
      testID="booking-detail"
      footer={links.length > 0 ? <Links links={links} onOpen={onOpen} /> : undefined}
    >
      {view.steps && <Steps steps={view.steps} />}

      <Card style={styles.card}>
        <View style={styles.between}>
          <Text style={[textStyle('label'), styles.ink, styles.grow]}>
            {t('bookingDetail.startsAt', { time: time(current.slotStart) })}
          </Text>
          <Badge
            label={t(`bookingDetail.status.${view.status}`)}
            tone={view.status === 'PAYMENT_REQUESTED' ? 'orange' : 'green'}
            testID="booking-status"
          />
        </View>
        <Text style={[textStyle('small'), styles.muted]} testID="booking-amount">
          {poster
            ? t('bookingDetail.totalPoster', { total: money(current.totalCents) })
            : t('bookingDetail.amountHelper', { amount: money(current.amountCents) })}
        </Text>
        {view.confirmationEmailed && (
          <Text style={[textStyle('small'), styles.muted]}>{t('checkout.held.email')}</Text>
        )}
      </Card>

      {held && (
        <Text style={[textStyle('caption'), styles.sys]}>
          {poster
            ? t('bookingDetail.heldPoster', { total: money(current.totalCents) })
            : t('bookingDetail.heldHelper', { amount: money(current.amountCents) })}
        </Text>
      )}

      {view.checkout && (
        <CheckoutNotice
          checkout={view.checkout}
          time={time}
          onPay={view.links.checkout ? () => onOpen('checkout') : undefined}
        />
      )}

      <PlaceCard place={view.place} />

      {view.trader && <HelperCard trader={view.trader} />}

      <Note tone="warn">{t('bookingDetail.safety')}</Note>
    </Screen>
  );
}

function Steps({ steps }: { steps: NonNullable<BookingDetail['steps']> }) {
  const { t } = useTranslation();
  // At large text the four columns are too narrow for whole words: list the steps instead.
  const stacked = useWindowDimensions().fontScale >= STACKED_STEPS_FONT_SCALE;
  return (
    <View
      accessibilityLabel={t('bookingDetail.stepsLabel')}
      style={stacked ? styles.stepsStacked : styles.steps}
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
            style={stacked ? styles.stepStacked : styles.step}
            testID={`booking-step-${step}-${state}`}
          >
            <View
              style={[
                styles.dot,
                state === 'done' && styles.dotDone,
                state === 'current' && styles.dotCurrent,
              ]}
              testID={`booking-step-dot-${step}`}
            />
            <Text
              style={[
                textStyle('caption'),
                !stacked && styles.stepLabel,
                state === 'current' ? styles.ink : styles.muted,
              ]}
            >
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
      <Heading style={[textStyle('caption'), styles.muted, styles.section]}>
        {t('bookingDetail.place.title')}
      </Heading>
      {place.kind === 'exact' ? (
        <>
          <Text style={[textStyle('label'), styles.ink]} testID="booking-address">
            {place.address}
          </Text>
          {place.onlyWhileBooked && (
            <Text style={[textStyle('small'), styles.muted]}>
              {t('bookingDetail.place.whileBooked')}
            </Text>
          )}
          {(place.accessDetails || place.contact) && <View style={styles.rule} />}
          {place.accessDetails ? (
            <Text style={[textStyle('body'), styles.ink]} testID="booking-access">
              {t('bookingDetail.place.access', { details: place.accessDetails })}
            </Text>
          ) : null}
          {place.contact && (
            <>
              <Text style={[textStyle('label'), styles.ink]}>
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
      <Heading style={[textStyle('caption'), styles.muted, styles.section]}>
        {t('bookingDetail.helperTitle')}
      </Heading>
      {trader.helperIsTrader ? (
        <TraderIdentity offer={trader} />
      ) : (
        <Text style={[textStyle('body'), styles.ink]}>{t('offers.trader.private')}</Text>
      )}
    </Card>
  );
}

/** The screen's actions, pinned at the bottom (mockup 5b, 5c `.foot`): the main one first. */
function Links({ links, onOpen }: { links: Link[]; onOpen(screen: BookingScreen): void }) {
  const { t } = useTranslation();
  const others = links.filter(({ primary }) => !primary);
  return (
    <>
      {others.length > 0 && (
        <View style={styles.secondaryLinks}>
          {others.map(({ link, screen }) => (
            <View key={link} style={styles.grow}>
              <Button
                variant="secondary"
                size="sm"
                label={t(`bookingDetail.links.${link}`)}
                onPress={() => onOpen(screen)}
                testID={`booking-link-${screen}`}
              />
            </View>
          ))}
        </View>
      )}
      {links
        .filter(({ primary }) => primary)
        .map(({ link, screen }) => (
          <Button
            key={link}
            label={t(`bookingDetail.links.${link}`)}
            onPress={() => onOpen(screen)}
            testID={`booking-link-${screen}`}
          />
        ))}
    </>
  );
}

const styles = StyleSheet.create({
  content: { gap: spacing.lg },
  ink: { color: colors.ink },
  muted: { color: colors.inkMuted },
  error: { color: colors.error },
  card: { gap: spacing.sm },
  grow: { flexGrow: 1, flexShrink: 1 },
  between: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: spacing.sm,
  },
  // The mockups' `.sys` pill: a system line in the booking's own colours.
  sys: {
    alignSelf: 'center',
    textAlign: 'center',
    color: colors.green,
    backgroundColor: colors.greenTint,
    borderRadius: radii.lg,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs + 2,
  },
  rule: { height: 1, backgroundColor: colors.line },
  // The mockups' small capitals section label ("ACCESS DETAILS").
  section: { textTransform: 'uppercase' },
  // The mockups' `.stepper`: one column per step, the dot above its label.
  steps: { flexDirection: 'row', alignItems: 'flex-start' },
  step: { flex: 1, alignItems: 'center', gap: spacing.xs },
  stepsStacked: { gap: spacing.sm },
  stepStacked: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  stepLabel: { textAlign: 'center' },
  dot: {
    width: 20,
    height: 20,
    // In the stacked list at large text a long label must not squeeze the dot.
    flexShrink: 0,
    borderRadius: radii.pill,
    borderWidth: 3,
    borderColor: colors.offWhite,
    backgroundColor: colors.line,
  },
  dotDone: { backgroundColor: colors.green },
  // Current: orange with an orange ring. Decorative; the label and its text say "current".
  dotCurrent: { backgroundColor: colors.orange, borderColor: colors.orangeTint },
  secondaryLinks: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
});

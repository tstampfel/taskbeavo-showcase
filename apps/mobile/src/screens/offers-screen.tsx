import { useQuery } from '@apollo/client/react';
import {
  OFFER_SORTS,
  TaskDocument,
  TaskOffersDocument,
  arrangeOffers,
  canAcceptOffer,
  msUntilNextExpiry,
  subscribeToTaskOffers,
  type OfferSort,
  type TaskOffersQuery,
} from '@taskbeavo/graphql';
import { formatDate, formatMoney, isLocale, defaultLocale, type Locale } from '@taskbeavo/i18n';
import { borderWidth, colors, minTapTarget, radii, spacing } from '@taskbeavo/ui-tokens';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
  useWindowDimensions,
} from 'react-native';
import { TraderIdentity, traderSummary } from '../components/trader-identity';
import { Badge, Button, Card, Screen } from '../components/ui';
import { textStyle } from '../components/ui/text-style';

type Offer = TaskOffersQuery['taskOffers'][number];

export interface OffersScreenProps {
  taskId: string;
  /** The poster tapped Accept: open checkout for this offer. */
  onAccept(offerId: string): void;
  /** Opens /{locale}/ranking in the in-app browser, like the legal pages. */
  openRanking(locale: Locale): void;
  /** The top bar's back button. */
  onBack?: () => void;
  /** For tests; the open/closed split and Accept depend on the time. */
  now?: () => Date;
}

/**
 * The poster's offers on their task (mockups 4a and 4c): open offers in the chosen order with
 * Accept, withdrawn and expired ones greyed at the end, and a side-by-side comparison. The list
 * stays live (subscribeToTaskOffers) and is refetched on reconnect and on return to the
 * foreground. Helper names and ratings aren't in the API yet (BEAV-144), so cards show the
 * trader label and a "new helper" state; each card names a trader helper's business, OIB and
 * registered address, or says a private helper isn't a trader, and how obligations are split
 * (ZZP čl. 61(1), BEAV-192). Prices are the all-in total the poster pays (price
 * plus service fee, from the API), with the helper's price below it; the link under the list
 * opens /{locale}/ranking, which explains the order (CRD art. 6a, BEAV-191).
 * `message` is the helper's note with contact details already masked by the server.
 */
export function OffersScreen({
  taskId,
  onAccept,
  openRanking,
  onBack,
  now = () => new Date(),
}: OffersScreenProps) {
  const { t, i18n } = useTranslation();
  const locale: Locale = isLocale(i18n.language) ? i18n.language : defaultLocale;
  const [sort, setSort] = useState<OfferSort>('newest');
  const [comparing, setComparing] = useState(false);
  const offers = useQuery(TaskOffersDocument, {
    variables: { taskId },
    fetchPolicy: 'cache-and-network',
    notifyOnNetworkStatusChange: true,
  });
  const task = useQuery(TaskDocument, { variables: { id: taskId } }).data?.task;
  const { subscribeToMore } = offers;
  const list = offers.data?.taskOffers;
  // Bumped when the next open offer expires, so it moves to the closed ones on screen.
  const [expiryTick, setExpiryTick] = useState(0);

  useEffect(() => subscribeToTaskOffers(subscribeToMore, taskId), [subscribeToMore, taskId]);
  useEffect(() => {
    const wait = list ? msUntilNextExpiry(list, now()) : null;
    if (wait === null) return;
    const timer = setTimeout(() => setExpiryTick((tick) => tick + 1), wait + EXPIRY_MARGIN_MS);
    return () => clearTimeout(timer);
    // `now` is a fresh default function each render; the list and the tick decide the timer.
  }, [list, expiryTick]);

  if (!list) {
    return (
      <Screen
        title={t('offers.metaTitle')}
        onBack={onBack}
        backLabel={t('offers.back')}
        testID={offers.error ? 'offers-error' : 'offers-loading'}
      >
        <View style={styles.center}>
          {offers.error && !offers.loading ? (
            <>
              <Text accessibilityRole="alert" style={[textStyle('body'), styles.ink]}>
                {t('offers.loadError')}
              </Text>
              <Button
                label={t('offers.retry')}
                onPress={() => void offers.refetch().catch(() => undefined)}
              />
            </>
          ) : (
            <ActivityIndicator accessibilityLabel={t('offers.metaTitle')} />
          )}
        </View>
      </Screen>
    );
  }

  const at = now();
  const { open, closed } = arrangeOffers(list, sort, at);
  const money = (cents: number) => formatMoney(cents, locale);
  const date = (value: unknown) => formatDate(String(value), locale);

  const taskLine = task
    ? [
        task.windowStart ? date(task.windowStart) : null,
        task.budgetCents !== null ? t('offers.budget', { amount: money(task.budgetCents) }) : null,
      ]
        .filter(Boolean)
        .join(' · ')
    : '';
  const rankingLink = (
    <Pressable
      accessibilityRole="link"
      onPress={() => openRanking(locale)}
      style={styles.link}
      testID="offers-ranking"
    >
      <Text style={[textStyle('label'), styles.linkText]}>{t('offers.rankingLink')}</Text>
    </Pressable>
  );

  return (
    <Screen
      title={
        comparing
          ? t('offers.compareTitle', { count: open.length })
          : t('offers.count', { count: list.length })
      }
      // Comparing is a view of the list: back returns to it (mockup 4c).
      onBack={comparing ? () => setComparing(false) : onBack}
      backLabel={comparing ? t('offers.backToList') : t('offers.back')}
      testID="offers-content"
    >
      {task && !comparing ? (
        <Card variant="flat" style={styles.taskCard}>
          <Text style={[textStyle('label'), styles.ink]}>{task.title}</Text>
          {taskLine ? <Text style={[textStyle('caption'), styles.muted]}>{taskLine}</Text> : null}
        </Card>
      ) : null}

      {list.length === 0 ? (
        <Text style={[textStyle('body'), styles.ink]}>{t('offers.empty')}</Text>
      ) : comparing ? (
        <Compare offers={open} at={at} money={money} date={date} onAccept={onAccept} />
      ) : (
        <>
          <View style={styles.toolbar}>
            <View
              accessible={false}
              accessibilityRole="radiogroup"
              accessibilityLabel={t('offers.sortLabel')}
              style={styles.chips}
            >
              {OFFER_SORTS.map((key) => (
                <Pressable
                  key={key}
                  accessibilityRole="radio"
                  accessibilityState={{ checked: sort === key }}
                  onPress={() => setSort(key)}
                  style={[styles.chip, sort === key && styles.chipOn]}
                >
                  <Text style={[textStyle('small'), sort === key ? styles.chipOnText : styles.ink]}>
                    {t(`offers.sort.${key}`)}
                  </Text>
                </Pressable>
              ))}
            </View>
            {open.length > 1 ? (
              <Button
                label={t('offers.compare')}
                variant="ghost"
                size="sm"
                onPress={() => setComparing(true)}
              />
            ) : null}
          </View>
          {rankingLink}
          {open.map((offer) => (
            <OfferCard
              key={offer.id}
              offer={offer}
              at={at}
              money={money}
              date={date}
              onAccept={onAccept}
            />
          ))}
          {closed.map((offer) => (
            <ClosedCard key={offer.id} offer={offer} money={money} />
          ))}
        </>
      )}
      {/* Empty or comparing, the explanation of the order still has a way in. */}
      {list.length === 0 || comparing ? rankingLink : null}
    </Screen>
  );
}

interface CardProps {
  offer: Offer;
  at: Date;
  money(cents: number): string;
  date(value: unknown): string;
  onAccept(offerId: string): void;
}

/** From this text scale on, the price goes under the helper's details instead of beside them. */
const STACKED_CARD_FONT_SCALE = 1.3;

function OfferCard({ offer, at, money, date, onAccept }: CardProps) {
  const { t } = useTranslation();
  const stacked = useWindowDimensions().fontScale >= STACKED_CARD_FONT_SCALE;
  return (
    <Card testID={`offer-card-${offer.id}`}>
      <View
        style={stacked ? styles.cardTopStacked : styles.cardTop}
        testID={`offer-top-${offer.id}`}
      >
        <View style={styles.grow}>
          <Badge
            label={t(offer.helperIsTrader ? 'offers.trader.business' : 'offers.trader.private')}
          />
          <TraderIdentity offer={offer} privateNotice />
          <Text style={[textStyle('small'), styles.muted]}>{t('offers.newHelper')}</Text>
        </View>
        <View style={stacked ? styles.priceBoxStacked : styles.priceBox}>
          <Text style={[textStyle('h1'), styles.price]}>{money(offer.totalCents)}</Text>
          <Text style={[textStyle('small'), styles.muted]}>{t('offers.totalNote')}</Text>
          <Text style={[textStyle('small'), styles.muted]}>
            {t('offers.helperPrice', { price: money(offer.priceCents) })}
          </Text>
        </View>
      </View>
      {/* Not in the saved cache (NOT_PERSISTED): absent until the refetch after a restart. */}
      {offer.message ? (
        <Text style={[textStyle('small'), styles.muted]}>{`“${offer.message}”`}</Text>
      ) : null}
      {offer.status === 'NEEDS_UPDATE' ? (
        <Text style={[textStyle('small'), styles.ink]}>{t('offers.needsUpdate')}</Text>
      ) : null}
      <Text style={[textStyle('small'), styles.muted]}>{t('offers.obligations')}</Text>
      <Text style={[textStyle('caption'), styles.muted, styles.end]}>
        {t('offers.expires', { date: date(offer.expiresAt) })}
      </Text>
      <Button
        label={t('offers.accept')}
        accessibilityLabel={t('offers.acceptLabel', { total: money(offer.totalCents) })}
        size="sm"
        disabled={!canAcceptOffer(offer, at)}
        onPress={() => onAccept(offer.id)}
      />
    </Card>
  );
}

function ClosedCard({ offer, money }: { offer: Offer; money(cents: number): string }) {
  const { t } = useTranslation();
  // Past its expiry but not yet marked by the server: it reads as expired.
  const status = offer.status === 'WITHDRAWN' ? 'WITHDRAWN' : 'EXPIRED';
  return (
    <Card testID={`offer-card-${offer.id}`} style={[styles.closed, styles.cardTop]}>
      <View style={styles.grow}>
        <Text style={[textStyle('label'), styles.ink]}>
          {t(offer.helperIsTrader ? 'offers.trader.business' : 'offers.trader.private')}
        </Text>
        <Text style={[textStyle('small'), styles.ink]}>{t(`offers.closed.${status}`)}</Text>
      </View>
      <Text style={[textStyle('h2'), styles.muted]}>{money(offer.totalCents)}</Text>
    </Card>
  );
}

function Compare({
  offers,
  at,
  money,
  date,
  onAccept,
}: Omit<CardProps, 'offer'> & { offers: Offer[] }) {
  const { t } = useTranslation();
  const rows: [string, (offer: Offer) => string][] = [
    [t('offers.row.price'), (offer) => money(offer.totalCents)],
    [t('offers.row.helperPrice'), (offer) => money(offer.priceCents)],
    [t('offers.row.rating'), () => t('offers.ratingNone')],
    [
      t('offers.row.status'),
      (offer) =>
        t(offer.helperIsTrader ? 'offers.statusShort.business' : 'offers.statusShort.private'),
    ],
    [t('offers.row.business'), (offer) => traderSummary(offer, t)],
    [t('offers.row.expires'), (offer) => date(offer.expiresAt)],
  ];
  return (
    <View testID="offers-compare" style={styles.compare}>
      {/* Three columns fit at 375pt; more scroll sideways. */}
      <ScrollView horizontal>
        <Card style={styles.table}>
          {rows.map(([label, value], index) => (
            <View key={label} style={styles.tableRow}>
              <Text style={[textStyle('small'), styles.muted, styles.rowLabel]}>{label}</Text>
              {offers.map((offer) => (
                <Text
                  key={offer.id}
                  // Screen readers get no column header, so each cell names its offer by price.
                  accessibilityLabel={
                    index === 0
                      ? undefined
                      : t('offers.cellLabel', {
                          row: label,
                          price: money(offer.totalCents),
                          value: value(offer),
                        })
                  }
                  style={[textStyle('label'), styles.ink, styles.cell]}
                >
                  {value(offer)}
                </Text>
              ))}
            </View>
          ))}
          <View style={styles.tableRow}>
            <View style={styles.rowLabel} importantForAccessibility="no" />
            {offers.map((offer) => (
              <View key={offer.id} style={styles.cell}>
                <Button
                  label={t('offers.accept')}
                  accessibilityLabel={t('offers.acceptLabel', { total: money(offer.totalCents) })}
                  size="sm"
                  disabled={!canAcceptOffer(offer, at)}
                  onPress={() => onAccept(offer.id)}
                />
              </View>
            ))}
          </View>
        </Card>
      </ScrollView>
      <Text style={[textStyle('caption'), styles.muted]}>{t('offers.compareOrder')}</Text>
    </View>
  );
}

const CELL_WIDTH = 96;
// Past the expiry instant, so the re-render sees the offer as expired.
const EXPIRY_MARGIN_MS = 50;

const styles = StyleSheet.create({
  center: { flex: 1, justifyContent: 'center', gap: spacing.lg },
  ink: { color: colors.ink },
  muted: { color: colors.inkMuted },
  // The mockups' `.price`: amounts in the text-safe orange (AA on white).
  price: { color: colors.orangeText },
  end: { textAlign: 'right' },
  grow: { flex: 1, gap: spacing.xs },
  taskCard: { gap: spacing.xs, padding: spacing.md },
  toolbar: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: spacing.sm,
  },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  chip: {
    minHeight: minTapTarget,
    justifyContent: 'center',
    paddingHorizontal: spacing.lg,
    borderRadius: radii.pill,
    borderWidth: borderWidth.thin,
    borderColor: colors.line,
    backgroundColor: colors.white,
  },
  chipOn: { backgroundColor: colors.green, borderColor: colors.green },
  chipOnText: { color: colors.white },
  cardTop: { flexDirection: 'row', alignItems: 'flex-start', gap: spacing.md },
  priceBox: { alignItems: 'flex-end', flexShrink: 1, gap: spacing.xs },
  cardTopStacked: { gap: spacing.md },
  priceBoxStacked: { gap: spacing.xs },
  link: { minHeight: minTapTarget, justifyContent: 'center' },
  linkText: { color: colors.green, textDecorationLine: 'underline' },
  // The mockup's .5 opacity fails AA on body text, so closed cards keep ink text on a tinted card.
  closed: { backgroundColor: colors.greyTint, alignItems: 'center' },
  compare: { gap: spacing.md },
  table: { gap: spacing.sm, padding: spacing.sm },
  tableRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  rowLabel: { width: CELL_WIDTH * 0.8 },
  cell: { width: CELL_WIDTH, textAlign: 'center' },
});

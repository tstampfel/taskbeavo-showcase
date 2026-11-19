import { useQuery } from '@apollo/client/react';
import {
  FeedHelperProfileDocument,
  TaskFeedDocument,
  apiErrorCode,
  nextPageVariables,
  type TaskCategory,
} from '@taskbeavo/graphql';
import { defaultLocale, isLocale, type Locale } from '@taskbeavo/i18n';
import { borderWidth, colors, minTapTarget, radii, spacing } from '@taskbeavo/ui-tokens';
import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ActivityIndicator, Pressable, StyleSheet, View } from 'react-native';
import { Button, Card, Heading, Screen } from '../components/ui';
import { Text } from '../components/ui/text';
import { textStyle } from '../components/ui/text-style';
import { listingArea, listingBudget, listingCategory, listingWhen } from '../tasks/listing-text';

/** The category chips, in the order the post-a-task wizard lists them. */
export const FEED_CATEGORIES: readonly TaskCategory[] = [
  'GARDEN',
  'ERRANDS',
  'REPAIRS',
  'CLEANING',
  'CHECK_IN',
];

const PAGE_SIZE = 20;

export interface TaskFeedScreenProps {
  /** The signed-in user's first name, for the greeting. */
  firstName: string;
  onOpenTask(taskId: string): void;
  onOpenSettings(): void;
  /** Above the feed: the terms banner, which must stay on home (limited mode). */
  banner?: ReactNode;
  /** Under the feed: the account row and language switcher, until the account tab exists. */
  account?: ReactNode;
}

/**
 * The helper's home feed (mockup 02, "Helper home"): open tasks in the service areas on their
 * helper profile, newest published first. The order is the API's and the same for every
 * helper (hard rule k), and the screen says so. Each card shows the approximate area only
 * (hard rule e). "More tasks" loads the next page; the cache appends it (typePolicies).
 *
 * Not built here (later tickets): the map view, "Not for me", distance and the poster's
 * name and rating.
 */
export function TaskFeedScreen({
  firstName,
  onOpenTask,
  onOpenSettings,
  banner,
  account,
}: TaskFeedScreenProps) {
  const { t, i18n } = useTranslation();
  const locale: Locale = isLocale(i18n.language) ? i18n.language : defaultLocale;
  const [category, setCategory] = useState<TaskCategory | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreFailed, setMoreFailed] = useState(false);
  // The feed is for helpers: a poster without a helper profile gets their own empty state.
  const helper = useQuery(FeedHelperProfileDocument, { fetchPolicy: 'cache-and-network' });
  const notHelper = helper.data !== undefined && helper.data.myHelperProfile === null;
  const feed = useQuery(TaskFeedDocument, {
    variables: { first: PAGE_SIZE, category },
    fetchPolicy: 'cache-and-network',
    notifyOnNetworkStatusChange: true,
  });
  const page = feed.data?.taskFeed;

  function loadMore() {
    const next = nextPageVariables(page);
    if (!next || loadingMore) return;
    setLoadingMore(true);
    setMoreFailed(false);
    void feed
      .fetchMore({ variables: { ...next, first: PAGE_SIZE, category } })
      .catch((error: unknown) => {
        // A cursor the API no longer knows: start again from the top.
        if (apiErrorCode(error) === 'INVALID_CURSOR') {
          return feed.refetch().catch(() => setMoreFailed(true));
        }
        setMoreFailed(true);
      })
      .finally(() => setLoadingMore(false));
  }

  const chips = (
    <View
      accessible={false}
      accessibilityRole="radiogroup"
      accessibilityLabel={t('taskFeed.filterLabel')}
      style={styles.chips}
    >
      {[null, ...FEED_CATEGORIES].map((key) => {
        const on = category === key;
        return (
          <Pressable
            key={key ?? 'all'}
            accessibilityRole="radio"
            accessibilityState={{ checked: on }}
            onPress={() => setCategory(key)}
            style={[styles.chip, on && styles.chipOn]}
            testID={`feed-category-${key ?? 'all'}`}
          >
            <Text style={[textStyle('small'), on ? styles.chipOnText : styles.ink]}>
              {key ? t(`enums.TaskCategory.${key}`) : t('taskFeed.all')}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );

  let body;
  if (!page) {
    body =
      feed.error && !feed.loading ? (
        <View style={styles.center} testID="feed-error">
          <Text accessibilityRole="alert" style={[textStyle('body'), styles.ink]}>
            {t('taskFeed.loadError')}
          </Text>
          <Button
            label={t('taskFeed.retry')}
            onPress={() => void feed.refetch().catch(() => undefined)}
          />
        </View>
      ) : (
        <View style={styles.center} testID="feed-loading">
          <ActivityIndicator accessibilityLabel={t('taskFeed.title')} />
        </View>
      );
  } else if (page.items.length === 0) {
    body = (
      <Card variant="flat" testID={notHelper ? 'feed-not-helper' : 'feed-empty'}>
        <Text style={[textStyle('label'), styles.ink]}>
          {notHelper ? t('taskFeed.notHelper') : t('taskFeed.empty')}
        </Text>
        <Text style={[textStyle('small'), styles.muted]}>
          {notHelper ? t('taskFeed.notHelperHint') : t('taskFeed.emptyHint')}
        </Text>
      </Card>
    );
  } else {
    body = (
      <View style={styles.list} testID="feed-content">
        {page.items.map((task) => {
          const area = listingArea(task, t);
          const facts = [area, listingWhen(task, t, locale)].filter(Boolean).join(' · ');
          return (
            <Pressable
              key={task.id}
              accessibilityRole="button"
              accessibilityLabel={[task.title, facts, listingBudget(task, t, locale)].join(', ')}
              onPress={() => onOpenTask(task.id)}
              style={styles.cardPress}
              testID={`feed-task-${task.id}`}
            >
              <Card style={styles.card}>
                <Text style={[textStyle('caption'), styles.green]}>{listingCategory(task, t)}</Text>
                <Text style={[textStyle('label'), styles.ink]}>{task.title}</Text>
                <Text style={[textStyle('small'), styles.muted]}>{facts}</Text>
                <Text style={[textStyle('label'), styles.green]}>
                  {listingBudget(task, t, locale)}
                </Text>
              </Card>
            </Pressable>
          );
        })}
        {page.hasMore ? (
          <Button
            label={t('browse.more')}
            variant="secondary"
            busy={loadingMore}
            onPress={loadMore}
            accessibilityHint={loadingMore ? t('taskFeed.loadingMore') : undefined}
            testID="feed-more"
          />
        ) : null}
        {moreFailed ? (
          <Text
            accessibilityRole="alert"
            style={[textStyle('small'), styles.ink]}
            testID="feed-more-error"
          >
            {t('taskFeed.loadMoreError')}
          </Text>
        ) : null}
      </View>
    );
  }

  return (
    <Screen testID="task-feed">
      <View style={styles.header}>
        <Heading style={[textStyle('h1'), styles.ink]}>
          {t('taskFeed.greeting', { name: firstName })}
        </Heading>
        <Button
          label={t('taskFeed.settings')}
          variant="ghost"
          size="sm"
          onPress={onOpenSettings}
          testID="open-settings"
        />
      </View>
      {banner}
      <Text style={[textStyle('h2'), styles.ink]}>{t('taskFeed.title')}</Text>
      <Text style={[textStyle('small'), styles.muted]} testID="feed-order">
        {t('taskFeed.order')}
      </Text>
      {chips}
      {body}
      {account}
    </Screen>
  );
}

const styles = StyleSheet.create({
  header: {
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
    paddingHorizontal: spacing.md,
    borderRadius: radii.pill,
    borderWidth: borderWidth.thin,
    borderColor: colors.control,
    backgroundColor: colors.white,
  },
  chipOn: { backgroundColor: colors.green, borderColor: colors.green },
  chipOnText: { color: colors.white },
  list: { gap: spacing.md },
  cardPress: { minHeight: minTapTarget },
  card: { gap: spacing.xs },
  center: { alignItems: 'center', gap: spacing.md, paddingVertical: spacing.xl },
  ink: { color: colors.ink },
  muted: { color: colors.inkMuted },
  green: { color: colors.green },
});

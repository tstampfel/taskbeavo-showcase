import { useQuery } from '@apollo/client/react';
import { TaskListingDocument, apiErrorCode } from '@taskbeavo/graphql';
import { defaultLocale, formatDate, isLocale, type Locale } from '@taskbeavo/i18n';
import { colors, radii, spacing } from '@taskbeavo/ui-tokens';
import { useTranslation } from 'react-i18next';
import { ActivityIndicator, Image, StyleSheet, View } from 'react-native';
import { Badge, Button, Card, Heading, Note, Screen } from '../components/ui';
import { Text } from '../components/ui/text';
import { textStyle } from '../components/ui/text-style';
import { listingArea, listingBudget, listingCategory, listingWhen } from '../tasks/listing-text';

export interface TaskDetailScreenProps {
  taskId: string;
  onBack(): void;
  /** The poster's own task: open its offers (mockup 4a). */
  onOpenOffers(): void;
}

/**
 * Task detail before booking (mockup 05, the task facts; chat is BEAV-104): what, when, the
 * approximate area, the budget, the description and photos. It reads `taskListing`, which has
 * no exact address, coordinates or access details, so nobody sees them here, not even a
 * helper who sent an offer (hard rule e); the booked helper gets them on the booking screen.
 * The poster sees "See offers" on their own task.
 */
export function TaskDetailScreen({ taskId, onBack, onOpenOffers }: TaskDetailScreenProps) {
  const { t, i18n } = useTranslation();
  const locale: Locale = isLocale(i18n.language) ? i18n.language : defaultLocale;
  const query = useQuery(TaskListingDocument, {
    variables: { id: taskId },
    fetchPolicy: 'cache-and-network',
    notifyOnNetworkStatusChange: true,
  });
  const task = query.data?.taskListing;

  if (!task) {
    const failed = query.error && !query.loading;
    const code = failed ? apiErrorCode(query.error) : undefined;
    return (
      <Screen
        title={t('taskListing.title')}
        onBack={onBack}
        backLabel={t('taskListing.back')}
        testID={failed ? 'task-detail-error' : 'task-detail-loading'}
      >
        <View style={styles.center}>
          {failed ? (
            <>
              <Text accessibilityRole="alert" style={[textStyle('body'), styles.ink]}>
                {code === 'TASK_NOT_FOUND'
                  ? t('errors.TASK_NOT_FOUND')
                  : t('taskListing.loadError')}
              </Text>
              {code === 'TASK_NOT_FOUND' ? null : (
                <Button
                  label={t('taskListing.retry')}
                  onPress={() => void query.refetch().catch(() => undefined)}
                />
              )}
            </>
          ) : (
            <ActivityIndicator accessibilityLabel={t('taskListing.title')} />
          )}
        </View>
      </Screen>
    );
  }

  const area = listingArea(task, t);
  return (
    <Screen
      title={t('taskListing.title')}
      onBack={onBack}
      backLabel={t('taskListing.back')}
      footer={
        task.isMine ? (
          <Button label={t('taskListing.seeOffers')} onPress={onOpenOffers} testID="see-offers" />
        ) : undefined
      }
      testID="task-detail"
    >
      <View style={styles.badges}>
        <Badge label={listingCategory(task, t)} />
      </View>
      <Heading style={[textStyle('h1'), styles.ink]}>{task.title}</Heading>
      <Card style={styles.facts}>
        <Text style={[textStyle('body'), styles.ink]} testID="task-when">
          {listingWhen(task, t, locale)}
        </Text>
        {area ? (
          <Text style={[textStyle('body'), styles.ink]} testID="task-area">
            {area}
          </Text>
        ) : null}
        <Text style={[textStyle('label'), styles.green]} testID="task-budget">
          {listingBudget(task, t, locale)}
        </Text>
        {task.publishedAt ? (
          <Text style={[textStyle('caption'), styles.muted]}>
            {t('taskListing.posted', { date: formatDate(String(task.publishedAt), locale) })}
          </Text>
        ) : null}
      </Card>
      <Note testID="task-approximate-note">{t('taskListing.approximateNote')}</Note>
      {task.description ? (
        <View style={styles.section}>
          <Text accessibilityRole="header" style={[textStyle('h2'), styles.ink]}>
            {t('taskListing.details')}
          </Text>
          <Text style={[textStyle('body'), styles.ink]}>{task.description}</Text>
        </View>
      ) : null}
      {task.photos.length > 0 ? (
        <View style={styles.section}>
          <Text accessibilityRole="header" style={[textStyle('h2'), styles.ink]}>
            {t('taskListing.photos')}
          </Text>
          <View style={styles.photos}>
            {task.photos.map((photo, index) => (
              <Image
                key={photo.id}
                source={{ uri: photo.url }}
                accessibilityLabel={t('taskListing.photoAlt', { number: index + 1 })}
                style={styles.photo}
              />
            ))}
          </View>
        </View>
      ) : null}
    </Screen>
  );
}

const PHOTO_SIZE = 96;

const styles = StyleSheet.create({
  badges: { flexDirection: 'row' },
  facts: { gap: spacing.xs },
  section: { gap: spacing.sm },
  photos: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  photo: { width: PHOTO_SIZE, height: PHOTO_SIZE, borderRadius: radii.md },
  center: { alignItems: 'center', gap: spacing.md, paddingVertical: spacing.xl },
  ink: { color: colors.ink },
  muted: { color: colors.inkMuted },
  green: { color: colors.green },
});

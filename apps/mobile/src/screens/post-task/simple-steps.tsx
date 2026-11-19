import type { UploadFieldsFragment } from '@taskbeavo/graphql';
import { colors, radii, spacing } from '@taskbeavo/ui-tokens';
import { useTranslation } from 'react-i18next';
import { Image, StyleSheet, View } from 'react-native';
import { Button, Heading, Input, Note } from '../../components/ui';
import { Text } from '../../components/ui/text';
import { textStyle } from '../../components/ui/text-style';
import {
  MAX_DESCRIPTION,
  MAX_PHOTOS,
  MAX_TITLE,
  TASK_CATEGORIES,
  type TaskDraft,
} from '../../tasks/post-task-model';
import { PhotoUploader } from '../../uploads/photo-uploader';
import { Choice, ChoiceGroup } from './choice';

export interface StepProps {
  draft: TaskDraft;
  change(patch: Partial<TaskDraft>): void;
  /** Translated problems of this step, shown once the poster tried to go on. */
  problems: string[];
}

/** Shown under a field once the poster tried to go on. */
export function problemFor(problems: string[], ...keys: string[]): string | undefined {
  return problems.find((key) => keys.includes(key));
}

/** Mockup 03, 3a: category and title. */
export function WhatStep({ draft, change, problems }: StepProps) {
  const { t } = useTranslation();
  const categoryProblem = problemFor(problems, 'postTask.errors.category');
  const titleProblem = problemFor(problems, 'postTask.errors.title');
  return (
    <>
      <Heading style={[textStyle('h1'), styles.ink]}>{t('postTask.what.heading')}</Heading>
      <ChoiceGroup label={t('postTask.what.categoryLabel')}>
        {TASK_CATEGORIES.map((category) => (
          <Choice
            key={category}
            label={t(`enums.TaskCategory.${category}`)}
            selected={draft.category === category}
            onPress={() => change({ category })}
            testID={`post-category-${category}`}
          />
        ))}
      </ChoiceGroup>
      {categoryProblem ? (
        <ProblemText testID="post-category-error" text={t(categoryProblem)} />
      ) : null}
      <Input
        label={t('postTask.what.titleLabel')}
        hint={t('postTask.what.titleHint')}
        value={draft.title}
        onChangeText={(title) => change({ title })}
        maxLength={MAX_TITLE}
        error={titleProblem ? t(titleProblem) : undefined}
        testID="post-title"
      />
    </>
  );
}

/** Mockup 03, 3b: description and photos. */
export function DetailsStep({ draft, change, problems }: StepProps) {
  const { t } = useTranslation();
  const problem = problemFor(problems, 'postTask.errors.description');
  function added(upload: UploadFieldsFragment) {
    if (draft.photos.length >= MAX_PHOTOS) return;
    change({ photos: [...draft.photos, { id: upload.id, url: upload.url ?? null }] });
  }
  return (
    <>
      <Heading style={[textStyle('h1'), styles.ink]}>{t('postTask.details.heading')}</Heading>
      <Input
        label={t('postTask.details.descriptionLabel')}
        hint={t('postTask.details.descriptionHint')}
        value={draft.description}
        onChangeText={(description) => change({ description })}
        maxLength={MAX_DESCRIPTION}
        multiline
        textAlignVertical="top"
        error={problem ? t(problem) : undefined}
        testID="post-description"
      />
      <Text style={[textStyle('label'), styles.ink]}>
        {t('postTask.details.photosLabel', { max: MAX_PHOTOS })}
      </Text>
      {draft.photos.map((photo, index) => (
        <View key={photo.id} style={styles.photoRow} testID={`post-photo-${index}`}>
          {photo.url ? (
            <Image
              accessibilityLabel={t('postTask.details.photoAlt', { number: index + 1 })}
              source={{ uri: photo.url }}
              style={styles.thumb}
            />
          ) : (
            <View style={[styles.thumb, styles.thumbEmpty]} />
          )}
          <Text style={[textStyle('small'), styles.grow, styles.ink]}>
            {t('postTask.details.photoAlt', { number: index + 1 })}
          </Text>
          <Button
            size="sm"
            variant="ghost"
            label={t('postTask.details.removePhoto')}
            accessibilityLabel={t('postTask.details.removePhotoLabel', { number: index + 1 })}
            onPress={() => change({ photos: draft.photos.filter((p) => p.id !== photo.id) })}
            testID={`post-photo-remove-${index}`}
          />
        </View>
      ))}
      {draft.photos.length < MAX_PHOTOS ? (
        <PhotoUploader purpose="TASK_PHOTO" onUploaded={added} preview={false} />
      ) : (
        <Text style={[textStyle('small'), styles.muted]} testID="post-photos-full">
          {t('postTask.details.photosFull')}
        </Text>
      )}
    </>
  );
}

/** Mockup 03, 3f: the poster types their own budget; the app never suggests one (rule k). */
export function BudgetStep({ draft, change, problems }: StepProps) {
  const { t } = useTranslation();
  const problem = problemFor(
    problems,
    'postTask.errors.budget',
    'errors.BUDGET_BELOW_MINIMUM',
    'postTask.errors.budgetTooHigh',
  );
  return (
    <>
      <Heading style={[textStyle('h1'), styles.ink]}>{t('postTask.budget.heading')}</Heading>
      <Input
        label={t('postTask.budget.label')}
        hint={t('postTask.budget.hint')}
        value={draft.budget}
        onChangeText={(budget) => change({ budget })}
        keyboardType="decimal-pad"
        inputMode="decimal"
        maxLength={10}
        error={problem ? t(problem) : undefined}
        testID="post-budget"
      />
      <Note>{t('postTask.budget.payment')}</Note>
    </>
  );
}

export function ProblemText({ text, testID }: { text: string; testID?: string }) {
  return (
    <Text
      accessibilityLiveRegion="polite"
      style={[textStyle('small'), styles.error]}
      testID={testID}
    >
      {text}
    </Text>
  );
}

const styles = StyleSheet.create({
  ink: { color: colors.ink },
  muted: { color: colors.inkMuted },
  error: { color: colors.error },
  grow: { flex: 1 },
  photoRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.md },
  thumb: { width: 64, height: 64, borderRadius: radii.md },
  thumbEmpty: { backgroundColor: colors.greyTint },
});

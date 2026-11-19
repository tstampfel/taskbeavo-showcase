import { useMutation } from '@apollo/client/react';
import { PostTaskDocument, apiErrorCode } from '@taskbeavo/graphql';
import { colors, radii, spacing } from '@taskbeavo/ui-tokens';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AccessibilityInfo, ActivityIndicator, StyleSheet, View } from 'react-native';
import { Button, Heading, Screen } from '../components/ui';
import { Text } from '../components/ui/text';
import { textStyle } from '../components/ui/text-style';
import { i18n as appI18n } from '../i18n';
import { nominatimSearch, type AddressSearch } from '../tasks/address-search';
import { deviceTaskDraftStore } from '../tasks/device-task-draft-store';
import {
  NUMBERED_STEPS,
  WIZARD_STEPS,
  emptyDraft,
  firstIncompleteStep,
  isDraftEmpty,
  stepProblems,
  toTaskInput,
  type TaskDraft,
  type WizardStep,
} from '../tasks/post-task-model';
import type { TaskDraftStore } from '../tasks/task-draft-store';
import { ReviewStep, WaitlistCard } from './post-task/review-step';
import { BudgetStep, DetailsStep, ProblemText, WhatStep } from './post-task/simple-steps';
import { WhenStep } from './post-task/when-step';
import { WhereStep } from './post-task/where-step';

export interface PostTaskScreenProps {
  /** Leaves the wizard; the draft stays saved. */
  onClose(): void;
  /** The task is posted: open its detail screen. */
  onPosted(taskId: string): void;
  onVerifyPhone(): void;
  onAcceptTerms(): void;
  draftStore?: TaskDraftStore;
  addressSearch?: AddressSearch;
  now?: () => Date;
}

type Phase = { name: 'loading' } | { name: 'resume'; saved: TaskDraft } | { name: 'editing' };

const defaultAddressSearch = nominatimSearch();

/**
 * Post a task (mockup 03, BEAV-86): category and title, details and photos, where and who,
 * when, the poster's own budget (hard rule k: the app never suggests, defaults or shows typical
 * prices), review, then postTask. Every change is saved on the phone, so the wizard resumes
 * where the poster left; posting or "Start again" deletes the draft, and so does logout
 * (resetClientState). The exact address and access details live only in SecureStore, and are
 * never logged or sent to Sentry (hard rule e).
 */
export function PostTaskScreen({
  onClose,
  onPosted,
  onVerifyPhone,
  onAcceptTerms,
  draftStore = deviceTaskDraftStore,
  addressSearch = defaultAddressSearch,
  now = () => new Date(),
}: PostTaskScreenProps) {
  const { t } = useTranslation();
  const [phase, setPhase] = useState<Phase>({ name: 'loading' });
  const [draft, setDraft] = useState<TaskDraft>(emptyDraft);
  const [showProblems, setShowProblems] = useState(false);
  const [backToReview, setBackToReview] = useState(false);
  const [postError, setPostError] = useState<string | null>(null);
  const [outOfArea, setOutOfArea] = useState(false);
  const finished = useRef(false);
  const [post, { loading: posting }] = useMutation(PostTaskDocument);

  useEffect(() => {
    let live = true;
    void draftStore.load().then((saved) => {
      if (!live) return;
      setPhase(saved && !isDraftEmpty(saved) ? { name: 'resume', saved } : { name: 'editing' });
    });
    return () => {
      live = false;
    };
  }, [draftStore]);

  useEffect(() => {
    if (phase.name !== 'editing' || finished.current) return;
    // A failed save only loses the draft on this phone; the wizard carries on.
    void draftStore.save(draft).catch(() => undefined);
  }, [draft, phase.name, draftStore]);

  function change(patch: Partial<TaskDraft>) {
    setDraft((current) => ({ ...current, ...patch }));
    if ('address' in patch || 'lat' in patch) setOutOfArea(false);
    setPostError(null);
  }

  function goTo(step: WizardStep) {
    setShowProblems(false);
    setDraft((current) => ({ ...current, step }));
  }

  const index = WIZARD_STEPS.indexOf(draft.step);
  const problems = showProblems ? stepProblems(draft.step, draft, now()) : [];

  function next() {
    const found = stepProblems(draft.step, draft, now());
    if (found.length > 0) {
      setShowProblems(true);
      AccessibilityInfo.announceForAccessibility(t(found[0]!));
      return;
    }
    if (backToReview) goTo('review');
    else goTo(WIZARD_STEPS[index + 1] ?? 'review');
  }

  function back() {
    if (index === 0) onClose();
    else if (backToReview) goTo('review');
    else goTo(WIZARD_STEPS[index - 1]!);
  }

  function edit(step: WizardStep) {
    setBackToReview(true);
    goTo(step);
  }

  async function submit() {
    const at = now();
    const input = toTaskInput(draft, at);
    if (!input) {
      const step = firstIncompleteStep(draft, at) ?? 'what';
      setBackToReview(true);
      setDraft((current) => ({ ...current, step }));
      setShowProblems(true);
      return;
    }
    setPostError(null);
    setOutOfArea(false);
    try {
      const { data } = await post({ variables: { input } });
      const id = data?.postTask.id;
      if (!id) throw new Error('postTask returned no task');
      finished.current = true;
      await draftStore.clear().catch(() => undefined);
      AccessibilityInfo.announceForAccessibility(t('postTask.posted'));
      onPosted(id);
    } catch (failure) {
      const code = apiErrorCode(failure);
      if (code === 'OUT_OF_SERVICE_AREA') {
        setOutOfArea(true);
        return;
      }
      setPostError(code ?? 'NETWORK');
    }
  }

  if (phase.name === 'loading') {
    return (
      <Screen title={t('postTask.title')} onBack={onClose} backLabel={t('postTask.close')}>
        <View style={styles.center} testID="post-loading">
          <ActivityIndicator accessibilityLabel={t('postTask.loading')} />
        </View>
      </Screen>
    );
  }

  if (phase.name === 'resume') {
    const title = phase.saved.title.trim();
    return (
      <Screen
        title={t('postTask.title')}
        onBack={onClose}
        backLabel={t('postTask.close')}
        testID="post-resume"
        footer={
          <>
            <Button
              label={t('postTask.resume.continue')}
              onPress={() => {
                setDraft(phase.saved);
                setPhase({ name: 'editing' });
              }}
              testID="post-resume-continue"
            />
            <Button
              variant="secondary"
              label={t('postTask.resume.startAgain')}
              onPress={() => {
                // Deletes the old draft with its address and access details before a new one.
                void draftStore.clear().catch(() => undefined);
                setDraft(emptyDraft());
                setPhase({ name: 'editing' });
              }}
              testID="post-resume-start-again"
            />
          </>
        }
      >
        <Heading style={[textStyle('h1'), styles.ink]}>{t('postTask.resume.title')}</Heading>
        <Text style={[textStyle('body'), styles.ink]}>
          {title ? t('postTask.resume.body', { title }) : t('postTask.resume.bodyUntitled')}
        </Text>
      </Screen>
    );
  }

  const reviewing = draft.step === 'review';
  const stepProps = { draft, change, problems };
  let body;
  switch (draft.step) {
    case 'what':
      body = <WhatStep {...stepProps} />;
      break;
    case 'details':
      body = <DetailsStep {...stepProps} />;
      break;
    case 'where':
      body = <WhereStep {...stepProps} addressSearch={addressSearch} />;
      break;
    case 'when':
      body = <WhenStep {...stepProps} now={now()} />;
      break;
    case 'budget':
      body = <BudgetStep {...stepProps} />;
      break;
    case 'review':
      body = <ReviewStep draft={draft} onEdit={edit} />;
      break;
  }

  const footer = reviewing ? (
    outOfArea ? null : (
      <Button
        label={t('postTask.review.post')}
        busy={posting}
        onPress={() => void submit()}
        testID="post-submit"
      />
    )
  ) : (
    <Button
      label={
        backToReview
          ? t('postTask.backToReview')
          : draft.step === 'budget'
            ? t('postTask.reviewTask')
            : t('postTask.next')
      }
      onPress={next}
      testID="post-next"
    />
  );

  return (
    <Screen
      title={reviewing ? t('postTask.review.title') : t('postTask.title')}
      onBack={back}
      backLabel={index === 0 ? t('postTask.close') : t('postTask.back')}
      footer={footer}
      testID="post-task"
    >
      {reviewing ? null : (
        <View
          accessible
          accessibilityRole="progressbar"
          accessibilityLabel={t('postTask.progress', { step: index + 1, total: NUMBERED_STEPS })}
          accessibilityValue={{ min: 1, max: NUMBERED_STEPS, now: index + 1 }}
          style={styles.progressRow}
          testID="post-progress"
        >
          <View style={styles.progress}>
            {Array.from({ length: NUMBERED_STEPS }, (_, i) => (
              <View key={i} style={[styles.bar, i <= index && styles.barOn]} />
            ))}
          </View>
          <Text style={[textStyle('caption'), styles.muted]}>
            {t('postTask.progressShort', { step: index + 1, total: NUMBERED_STEPS })}
          </Text>
        </View>
      )}
      {body}
      {reviewing && outOfArea ? (
        <WaitlistCard draft={draft} onChangeAddress={() => edit('where')} />
      ) : null}
      {reviewing && postError ? (
        <View style={styles.errorBox} testID="post-error">
          <ProblemText
            text={
              appI18n.exists(`errors.${postError}`)
                ? t(`errors.${postError}`)
                : t('postTask.review.postFailed')
            }
          />
          {postError === 'PHONE_NOT_VERIFIED' ? (
            <Button
              variant="secondary"
              label={t('postTask.review.verifyPhone')}
              onPress={onVerifyPhone}
              testID="post-verify-phone"
            />
          ) : null}
          {postError === 'TERMS_UPDATE_REQUIRED' ? (
            <Button
              variant="secondary"
              label={t('postTask.review.acceptTerms')}
              onPress={onAcceptTerms}
              testID="post-accept-terms"
            />
          ) : null}
        </View>
      ) : null}
    </Screen>
  );
}

const styles = StyleSheet.create({
  center: { alignItems: 'center', paddingVertical: spacing.xl },
  ink: { color: colors.ink },
  muted: { color: colors.inkMuted },
  progressRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.md },
  progress: { flex: 1, flexDirection: 'row', gap: spacing.xs },
  bar: { flex: 1, height: 6, borderRadius: radii.pill, backgroundColor: colors.line },
  barOn: { backgroundColor: colors.green },
  errorBox: { gap: spacing.md },
});

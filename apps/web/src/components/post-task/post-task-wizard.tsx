'use client';

import { useMutation, useQuery } from '@apollo/client/react';
import {
  NUMBERED_STEPS,
  PostTaskDocument,
  ViewerDocument,
  WIZARD_STEPS,
  apiErrorCode,
  emptyDraft,
  firstIncompleteStep,
  isDraftEmpty,
  stepProblems,
  toTaskInput,
  type TaskDraft,
  type WizardStep,
} from '@taskbeavo/graphql';
import type { Locale } from '@taskbeavo/i18n';
import { useTranslations } from 'next-intl';
import { useEffect, useRef, useState } from 'react';
import { Link, useRouter } from '../../i18n/navigation';
import { Button } from '../ui';
import { nominatimSearch, type AddressSearch } from './address-search';
import styles from './post-task.module.css';
import { ReviewStep, WaitlistCard } from './review-step';
import { BudgetStep, DetailsStep, ProblemText, WhatStep, WhenStep } from './steps';
import { browserTaskDraftStore, type TaskDraftStore } from './task-draft-store';
import { WhereStep } from './where-step';

export interface PostTaskWizardProps {
  locale: Locale;
  draftStore?: TaskDraftStore;
  addressSearch?: AddressSearch;
  now?: () => Date;
}

type Phase = { name: 'loading' } | { name: 'resume'; saved: TaskDraft } | { name: 'editing' };

const defaultAddressSearch = nominatimSearch();

/**
 * Post a task on the web (mockup 03, BEAV-87), the same steps and rules as the mobile wizard
 * (BEAV-86): category and title, details and photos, where and who, when, the poster's own
 * budget (hard rule k: no suggested, default or typical amount), review, then postTask, which
 * opens the new task. Every change is saved in this browser without the access details (the
 * exact address only for this tab); posting, "Start again" and signing out delete the draft.
 * Nothing here is logged or sent to Sentry (hard rule e).
 */
export function PostTaskWizard({
  locale,
  draftStore = browserTaskDraftStore,
  addressSearch = defaultAddressSearch,
  now = () => new Date(),
}: PostTaskWizardProps) {
  const t = useTranslations();
  const tErrors = useTranslations('errors');
  const router = useRouter();
  const viewer = useQuery(ViewerDocument);
  const [phase, setPhase] = useState<Phase>({ name: 'loading' });
  const [draft, setDraft] = useState<TaskDraft>(emptyDraft);
  const [showProblems, setShowProblems] = useState(false);
  const [backToReview, setBackToReview] = useState(false);
  const [postError, setPostError] = useState<string | null>(null);
  const [outOfArea, setOutOfArea] = useState(false);
  const finished = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const moved = useRef(false);
  const [post, { loading: posting }] = useMutation(PostTaskDocument);

  // The draft lives in this browser only, so it's read after the first render.
  useEffect(() => {
    const saved = draftStore.load();
    setPhase(saved && !isDraftEmpty(saved) ? { name: 'resume', saved } : { name: 'editing' });
  }, [draftStore]);

  useEffect(() => {
    if (phase.name !== 'editing' || finished.current) return;
    draftStore.save(draft);
  }, [draft, phase.name, draftStore]);

  // A new step announces itself: focus goes to its heading (not on the first one shown).
  useEffect(() => {
    if (phase.name !== 'editing') return;
    if (moved.current) heading.current?.focus();
  }, [draft.step, phase.name]);

  if (phase.name === 'loading' || (viewer.loading && !viewer.data)) {
    return <div className={styles.main} aria-busy="true" data-testid="post-loading" />;
  }

  if (!viewer.data?.viewer) {
    return (
      <div className={styles.main} data-testid="post-sign-in">
        <h2 className={styles.heading}>{t('postTask.signInNeeded')}</h2>
        <div>
          <Link
            href={{ pathname: '/sign-in', query: { next: '/tasks/new' } }}
            className={styles.link}
            data-testid="post-sign-in-link"
          >
            {t('postTask.signIn')}
          </Link>
        </div>
      </div>
    );
  }

  function change(patch: Partial<TaskDraft>) {
    setDraft((current) => ({ ...current, ...patch }));
    if ('address' in patch || 'lat' in patch) setOutOfArea(false);
    setPostError(null);
  }

  function goTo(step: WizardStep) {
    moved.current = true;
    setShowProblems(false);
    // Back at the review, an edit is done: Next and Back walk the steps again from here.
    if (step === 'review') setBackToReview(false);
    setDraft((current) => ({ ...current, step }));
  }

  const index = WIZARD_STEPS.indexOf(draft.step);
  const problems = showProblems ? stepProblems(draft.step, draft, now()) : [];

  function next() {
    if (stepProblems(draft.step, draft, now()).length > 0) {
      setShowProblems(true);
      return;
    }
    if (backToReview) goTo('review');
    else goTo(WIZARD_STEPS[index + 1] ?? 'review');
  }

  function back() {
    if (index > 0) goTo(WIZARD_STEPS[index - 1]!);
  }

  function edit(step: WizardStep) {
    setBackToReview(true);
    goTo(step);
  }

  async function submit() {
    const at = now();
    const input = toTaskInput(draft, at);
    if (!input) {
      setBackToReview(true);
      goTo(firstIncompleteStep(draft, at) ?? 'what');
      setShowProblems(true);
      return;
    }
    setPostError(null);
    setOutOfArea(false);
    try {
      const { data } = await post({ variables: { input } });
      const id = data?.postTask.task.id;
      if (!id) throw new Error('postTask returned no task');
      finished.current = true;
      draftStore.clear();
      router.push(`/tasks/${id}`);
    } catch (failure) {
      const code = apiErrorCode(failure);
      if (code === 'OUT_OF_SERVICE_AREA') setOutOfArea(true);
      else setPostError(code ?? 'NETWORK');
    }
  }

  if (phase.name === 'resume') {
    const title = phase.saved.title.trim();
    return (
      <div className={styles.main} data-testid="post-resume">
        <h2 className={styles.heading}>{t('postTask.resume.title')}</h2>
        <p className={styles.body}>
          {title ? t('postTask.resume.bodyWeb', { title }) : t('postTask.resume.bodyUntitledWeb')}
        </p>
        <div className={styles.actions}>
          <Button
            variant="secondary"
            onClick={() => {
              draftStore.clear();
              setDraft(emptyDraft());
              setPhase({ name: 'editing' });
            }}
            data-testid="post-resume-start-again"
          >
            {t('postTask.resume.startAgain')}
          </Button>
          <Button
            onClick={() => {
              setDraft(phase.saved);
              setPhase({ name: 'editing' });
            }}
            data-testid="post-resume-continue"
          >
            {t('postTask.resume.continue')}
          </Button>
        </div>
      </div>
    );
  }

  const reviewing = draft.step === 'review';
  const stepProps = { draft, change, problems, headingRef: heading };
  let body;
  switch (draft.step) {
    case 'what':
      body = <WhatStep {...stepProps} />;
      break;
    case 'details':
      body = <DetailsStep {...stepProps} />;
      break;
    case 'where':
      body = <WhereStep {...stepProps} addressSearch={addressSearch} locale={locale} />;
      break;
    case 'when':
      body = <WhenStep {...stepProps} now={now()} locale={locale} />;
      break;
    case 'budget':
      body = <BudgetStep {...stepProps} />;
      break;
    case 'review':
      body = <ReviewStep draft={draft} locale={locale} onEdit={edit} headingRef={heading} />;
      break;
  }

  // While editing from the review, Next already says "Back to review".
  const showBack = !backToReview && index > 0;
  const nextLabel = backToReview
    ? t('postTask.backToReview')
    : draft.step === 'budget'
      ? t('postTask.reviewTask')
      : t('postTask.next');

  return (
    <div className={styles.main} data-testid="post-task" data-step={draft.step}>
      {reviewing ? null : (
        <div className={styles.progress}>
          <div
            className={styles.bars}
            role="progressbar"
            aria-label={t('postTask.progress', { step: index + 1, total: NUMBERED_STEPS })}
            aria-valuemin={1}
            aria-valuemax={NUMBERED_STEPS}
            aria-valuenow={index + 1}
            data-testid="post-progress"
          >
            {Array.from({ length: NUMBERED_STEPS }, (_, i) => (
              <span key={i} className={i <= index ? `${styles.bar} ${styles.barOn}` : styles.bar} />
            ))}
          </div>
          <span className={`${styles.caption} ${styles.muted}`} aria-hidden="true">
            {t('postTask.progressShort', { step: index + 1, total: NUMBERED_STEPS })}
          </span>
        </div>
      )}
      <div className={styles.step}>{body}</div>
      {reviewing && outOfArea ? (
        <WaitlistCard draft={draft} locale={locale} onChangeAddress={() => edit('where')} />
      ) : null}
      {reviewing && postError ? (
        <div className={styles.group} data-testid="post-error">
          <ProblemText
            text={tErrors.has(postError) ? tErrors(postError) : t('postTask.review.postFailed')}
          />
          {postError === 'PHONE_NOT_VERIFIED' ? (
            <div>
              <Link
                href={{ pathname: '/verify-phone', query: { next: '/tasks/new' } }}
                className={styles.link}
                data-testid="post-verify-phone"
              >
                {t('postTask.review.verifyPhone')}
              </Link>
            </div>
          ) : null}
          {postError === 'TERMS_UPDATE_REQUIRED' ? (
            <div>
              <Link
                href={{ pathname: '/terms-update', query: { next: '/tasks/new' } }}
                className={styles.link}
                data-testid="post-accept-terms"
              >
                {t('postTask.review.acceptTerms')}
              </Link>
            </div>
          ) : null}
        </div>
      ) : null}
      <div className={styles.actions}>
        {showBack ? (
          <Button variant="secondary" onClick={back} data-testid="post-back">
            {t('postTask.back')}
          </Button>
        ) : null}
        {reviewing ? (
          outOfArea ? null : (
            <Button
              disabled={posting}
              aria-busy={posting}
              onClick={() => void submit()}
              data-testid="post-submit"
            >
              {t('postTask.review.post')}
            </Button>
          )
        ) : (
          <Button onClick={next} data-testid="post-next">
            {nextLabel}
          </Button>
        )}
      </div>
    </div>
  );
}

'use client';

import { useMutation, useQuery } from '@apollo/client/react';
import {
  JoinWaitlistDocument,
  MyCareContactsDocument,
  ViewerDocument,
  apiErrorCode,
  parseBudgetCents,
  windowRange,
  type TaskDraft,
  type WizardStep,
} from '@taskbeavo/graphql';
import { formatMoney, toApiLocale, type Locale } from '@taskbeavo/i18n';
import { useTranslations } from 'next-intl';
import { useState, type Ref } from 'react';
import { listingWhen, type Listing } from '../tasks/listing-text';
import { Button, Card, Input } from '../ui';
import styles from './post-task.module.css';
import { ProblemText, StepHeading } from './steps';

const EXCERPT_LENGTH = 140;

/** The start of a long description, so the review stays short; Edit shows all of it. */
export function excerpt(text: string): string {
  return text.length > EXCERPT_LENGTH ? `${text.slice(0, EXCERPT_LENGTH).trimEnd()}…` : text;
}

export interface ReviewSection {
  step: WizardStep;
  label: string;
  main: string;
  detail?: string;
}

/** next-intl's `t` for the whole catalogue, as far as the review needs it. */
type T = (key: string, values?: Record<string, string | number>) => string;

/** What the review lists, one part per step. Pure, so it's tested in every language. */
export function reviewSections(
  draft: TaskDraft,
  t: T,
  locale: Locale,
  contactName: string | null,
): ReviewSection[] {
  const range =
    draft.dateMode === 'date' && draft.date && draft.timeWindow
      ? windowRange(draft.date, draft.timeWindow)
      : null;
  const when = listingWhen(
    {
      flexibleDate: range === null,
      windowStart: range?.start.toISOString() ?? null,
      windowEnd: range?.end.toISOString() ?? null,
    } as Listing,
    t,
    locale,
  );
  const budgetCents = parseBudgetCents(draft.budget);
  return [
    {
      step: 'what',
      label: t('postTask.review.what'),
      main: draft.title.trim(),
      detail: draft.category ? t(`enums.TaskCategory.${draft.category}`) : undefined,
    },
    {
      step: 'details',
      label: t('postTask.review.details'),
      main: excerpt(draft.description.trim()),
      detail:
        draft.photos.length > 0
          ? t('postTask.review.photos', { count: draft.photos.length })
          : t('postTask.review.noPhotos'),
    },
    {
      step: 'where',
      label: t('postTask.review.where'),
      main: draft.address,
      // A saved contact whose name hasn't loaded yet is still sent: never "Me" for it (R1-4).
      detail: contactName
        ? t('postTask.review.onSite', { name: contactName })
        : draft.careContactId
          ? t('postTask.review.onSiteSaved')
          : t('postTask.where.noContact'),
    },
    { step: 'when', label: t('postTask.review.when'), main: when },
    {
      step: 'budget',
      label: t('postTask.review.budget'),
      main: budgetCents === null ? '' : formatMoney(budgetCents, locale),
    },
  ];
}

/** Mockup 03, 3g: what the poster is about to post, each part with Edit. */
export function ReviewStep({
  draft,
  locale,
  onEdit,
  headingRef,
}: {
  draft: TaskDraft;
  locale: Locale;
  onEdit(step: WizardStep): void;
  headingRef: Ref<HTMLHeadingElement>;
}) {
  const t = useTranslations();
  const contacts = useQuery(MyCareContactsDocument, { fetchPolicy: 'cache-first' });
  const contact = contacts.data?.myCareContacts.find((c) => c.id === draft.careContactId);
  const sections = reviewSections(draft, t as T, locale, contact?.name ?? null);
  return (
    <>
      <StepHeading headingRef={headingRef}>{t('postTask.review.title')}</StepHeading>
      <Card data-testid="post-review">
        <ul className={styles.reviewList}>
          {sections.map((section) => (
            <li key={section.step} className={styles.reviewItem}>
              <div className={styles.reviewText}>
                <span className={`${styles.caption} ${styles.muted}`}>{section.label}</span>
                <span className={styles.label} data-testid={`post-review-${section.step}`}>
                  {section.main}
                </span>
                {section.detail ? (
                  <span className={`${styles.small} ${styles.muted}`}>{section.detail}</span>
                ) : null}
              </div>
              <Button
                variant="ghost"
                size="sm"
                aria-label={t('postTask.review.editSection', { section: section.label })}
                onClick={() => onEdit(section.step)}
                data-testid={`post-edit-${section.step}`}
              >
                {t('postTask.review.edit')}
              </Button>
            </li>
          ))}
        </ul>
      </Card>
      <p className={`${styles.small} ${styles.muted}`}>{t('postTask.review.privacy')}</p>
    </>
  );
}

/**
 * Mockup 03, 3i: the address is outside every service area (postTask said OUT_OF_SERVICE_AREA,
 * hard rule j). The poster can join the waitlist for the town or change the address; the
 * consent covers one launch email only.
 */
export function WaitlistCard({
  draft,
  locale,
  onChangeAddress,
}: {
  draft: TaskDraft;
  locale: Locale;
  onChangeAddress(): void;
}) {
  const t = useTranslations();
  const tErrors = useTranslations('errors');
  const viewer = useQuery(ViewerDocument, { fetchPolicy: 'cache-first' }).data?.viewer;
  const [city, setCity] = useState(draft.city);
  const [email, setEmail] = useState<string | null>(null);
  const [consent, setConsent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [join, { data, loading }] = useMutation(JoinWaitlistDocument);
  const shownEmail = email ?? viewer?.email ?? '';
  const ready =
    city.trim() !== '' && shownEmail.trim() !== '' && consent && draft.category !== null;

  async function submit() {
    if (!draft.category || !ready || loading) return;
    setError(null);
    try {
      await join({
        variables: {
          input: {
            email: shownEmail.trim(),
            city: city.trim(),
            countryCode: 'HR',
            category: draft.category,
            locale: toApiLocale(locale),
            consentLaunchEmail: consent,
          },
        },
      });
    } catch (failure) {
      const code = apiErrorCode(failure);
      setError(code && tErrors.has(code) ? tErrors(code) : t('postTask.waitlist.failed'));
    }
  }

  return (
    <Card className={styles.group} data-testid="post-waitlist">
      <h3 className={styles.h3}>{t('postTask.waitlist.heading')}</h3>
      <p className={styles.small} role="alert">
        {tErrors('OUT_OF_SERVICE_AREA')}
      </p>
      {data?.joinWaitlist ? (
        <p
          role="status"
          className={`${styles.label} ${styles.green}`}
          data-testid="post-waitlist-joined"
        >
          {t('postTask.waitlist.joined', { city: data.joinWaitlist.city })}
        </p>
      ) : (
        <form
          className={styles.group}
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <Input
            label={t('postTask.waitlist.city')}
            value={city}
            onChange={(event) => setCity(event.target.value)}
            maxLength={100}
            data-testid="post-waitlist-city"
          />
          <Input
            label={t('postTask.waitlist.email')}
            value={shownEmail}
            onChange={(event) => setEmail(event.target.value)}
            type="email"
            autoComplete="email"
            data-testid="post-waitlist-email"
          />
          <label className={styles.checkbox}>
            <input
              type="checkbox"
              checked={consent}
              onChange={(event) => setConsent(event.target.checked)}
              data-testid="post-waitlist-consent"
            />
            <span>{t('postTask.waitlist.consent')}</span>
          </label>
          {error ? <ProblemText text={error} /> : null}
          <Button
            type="submit"
            disabled={!ready || loading}
            aria-busy={loading}
            data-testid="post-waitlist-join"
          >
            {t('postTask.waitlist.join')}
          </Button>
        </form>
      )}
      <Button
        variant="secondary"
        onClick={onChangeAddress}
        data-testid="post-waitlist-change-address"
      >
        {t('postTask.waitlist.changeAddress')}
      </Button>
    </Card>
  );
}

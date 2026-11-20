'use client';

import {
  MAX_DESCRIPTION,
  MAX_PHOTOS,
  MAX_TITLE,
  TASK_CATEGORIES,
  TIME_WINDOWS,
  addDays,
  lastBookableDay,
  windowOpen,
  windowRange,
  zagrebDay,
  type TaskDraft,
  type TimeWindow,
  type UploadFieldsFragment,
} from '@taskbeavo/graphql';
import { formatTime, type Locale } from '@taskbeavo/i18n';
import { useTranslations } from 'next-intl';
import { useId, useState, type ReactNode, type Ref, type TextareaHTMLAttributes } from 'react';
import { PhotoUploader } from '../../uploads/photo-uploader';
import { Button, Card, InfoIcon, Input, Note } from '../ui';
import styles from './post-task.module.css';

export interface StepProps {
  draft: TaskDraft;
  change(patch: Partial<TaskDraft>): void;
  /** Catalogue keys of this step's problems, shown once the poster tried to go on. */
  problems: string[];
  /** The step's heading; the wizard focuses it when the step opens. */
  headingRef: Ref<HTMLHeadingElement>;
}

/** The first of `keys` among the step's problems. */
export function problemFor(problems: string[], ...keys: string[]): string | undefined {
  return problems.find((key) => keys.includes(key));
}

export function StepHeading({
  headingRef,
  children,
}: {
  headingRef: Ref<HTMLHeadingElement>;
  children: ReactNode;
}) {
  return (
    <h2 ref={headingRef} tabIndex={-1} className={styles.heading} data-testid="post-step-heading">
      {children}
    </h2>
  );
}

export function ProblemText({ text, testId }: { text: string; testId?: string }) {
  return (
    <p role="alert" className={`${styles.small} ${styles.error}`} data-testid={testId}>
      {text}
    </p>
  );
}

/** A group of radio buttons drawn as chips (or full-width rows), with a visible legend. */
export function ChoiceGroup({
  legend,
  hideLegend = false,
  rows = false,
  children,
  testId,
}: {
  legend: string;
  hideLegend?: boolean;
  rows?: boolean;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <fieldset
      className={rows ? `${styles.choices} ${styles.choicesColumn}` : styles.choices}
      data-testid={testId}
    >
      <legend className={hideLegend ? styles.visuallyHidden : styles.legend}>{legend}</legend>
      {children}
    </fieldset>
  );
}

export function Choice({
  name,
  label,
  detail,
  checked,
  disabled = false,
  row = false,
  onSelect,
  testId,
}: {
  name: string;
  label: string;
  detail?: string;
  checked: boolean;
  disabled?: boolean;
  row?: boolean;
  onSelect(): void;
  testId: string;
}) {
  return (
    <label className={row ? `${styles.choice} ${styles.row}` : styles.choice}>
      <input
        type="radio"
        name={name}
        checked={checked}
        disabled={disabled}
        onChange={onSelect}
        data-testid={testId}
      />
      <span>{label}</span>
      {detail ? <span className={styles.choiceDetail}>{detail}</span> : null}
    </label>
  );
}

/** A labelled multi-line field, wired like components/ui/input. */
export function TextArea({
  label,
  hint,
  error,
  ...props
}: Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'aria-invalid'> & {
  label: string;
  hint?: string;
  error?: string;
}) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  return (
    <div className={styles.field}>
      <label htmlFor={id} className={styles.fieldLabel}>
        {label}
      </label>
      <textarea
        {...props}
        id={id}
        className={styles.textarea}
        aria-invalid={error ? true : undefined}
        aria-describedby={[errorId, hintId].filter(Boolean).join(' ') || undefined}
      />
      {hint ? (
        <p id={hintId} className={`${styles.small} ${styles.muted}`}>
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={errorId} role="alert" className={`${styles.small} ${styles.error}`}>
          {error}
        </p>
      ) : null}
    </div>
  );
}

/** Mockup 03, 3a: category and title. */
export function WhatStep({ draft, change, problems, headingRef }: StepProps) {
  const t = useTranslations();
  const categoryProblem = problemFor(problems, 'postTask.errors.category');
  const titleProblem = problemFor(problems, 'postTask.errors.title');
  return (
    <>
      <StepHeading headingRef={headingRef}>{t('postTask.what.heading')}</StepHeading>
      <div className={styles.group}>
        <ChoiceGroup legend={t('postTask.what.categoryLabel')}>
          {TASK_CATEGORIES.map((category) => (
            <Choice
              key={category}
              name="category"
              label={t(`enums.TaskCategory.${category}`)}
              checked={draft.category === category}
              onSelect={() => change({ category })}
              testId={`post-category-${category}`}
            />
          ))}
        </ChoiceGroup>
        {categoryProblem ? (
          <ProblemText text={t(categoryProblem)} testId="post-category-error" />
        ) : null}
      </div>
      <Input
        label={t('postTask.what.titleLabel')}
        hint={t('postTask.what.titleHint')}
        value={draft.title}
        onChange={(event) => change({ title: event.target.value })}
        maxLength={MAX_TITLE}
        error={titleProblem ? t(titleProblem) : undefined}
        data-testid="post-title"
      />
    </>
  );
}

/** Mockup 03, 3b: description and photos. */
export function DetailsStep({ draft, change, problems, headingRef }: StepProps) {
  const t = useTranslations();
  const problem = problemFor(problems, 'postTask.errors.description');

  function added(upload: UploadFieldsFragment) {
    if (draft.photos.length >= MAX_PHOTOS) return;
    change({ photos: [...draft.photos, { id: upload.id, url: upload.url ?? null }] });
  }

  return (
    <>
      <StepHeading headingRef={headingRef}>{t('postTask.details.heading')}</StepHeading>
      <TextArea
        label={t('postTask.details.descriptionLabel')}
        hint={t('postTask.details.descriptionHint')}
        value={draft.description}
        onChange={(event) => change({ description: event.target.value })}
        maxLength={MAX_DESCRIPTION}
        rows={5}
        error={problem ? t(problem) : undefined}
        data-testid="post-description"
      />
      <div className={styles.group}>
        <p className={styles.label}>{t('postTask.details.photosLabel', { max: MAX_PHOTOS })}</p>
        {draft.photos.map((photo, index) => (
          <div key={photo.id} className={styles.photoRow} data-testid={`post-photo-${index}`}>
            {photo.url ? (
              // Signed R2 URLs aren't a next/image remote pattern; the uploader does the same.
              // eslint-disable-next-line @next/next/no-img-element
              <img src={photo.url} alt="" className={styles.thumb} />
            ) : (
              <span className={styles.thumb} aria-hidden="true" />
            )}
            <span className={`${styles.small} ${styles.grow}`}>
              {t('postTask.details.photoAlt', { number: index + 1 })}
            </span>
            <Button
              variant="ghost"
              size="sm"
              aria-label={t('postTask.details.removePhotoLabel', { number: index + 1 })}
              onClick={() => change({ photos: draft.photos.filter((p) => p.id !== photo.id) })}
              data-testid={`post-photo-remove-${index}`}
            >
              {t('postTask.details.removePhoto')}
            </Button>
          </div>
        ))}
        {draft.photos.length < MAX_PHOTOS ? (
          <PhotoUploader purpose="TASK_PHOTO" onUploaded={added} />
        ) : (
          <p className={`${styles.small} ${styles.muted}`} data-testid="post-photos-full">
            {t('postTask.details.photosFull')}
          </p>
        )}
      </div>
    </>
  );
}

/** Mockup 03, 3f: the poster types their own budget; the app never suggests one (rule k). */
export function BudgetStep({ draft, change, problems, headingRef }: StepProps) {
  const t = useTranslations();
  const problem = problemFor(
    problems,
    'postTask.errors.budget',
    'errors.BUDGET_BELOW_MINIMUM',
    'postTask.errors.budgetTooHigh',
  );
  return (
    <>
      <StepHeading headingRef={headingRef}>{t('postTask.budget.heading')}</StepHeading>
      <Input
        label={t('postTask.budget.label')}
        hint={t('postTask.budget.hint')}
        value={draft.budget}
        onChange={(event) => change({ budget: event.target.value })}
        inputMode="decimal"
        autoComplete="off"
        maxLength={10}
        error={problem ? t(problem) : undefined}
        data-testid="post-budget"
      />
      <Note icon={<InfoIcon />}>{t('postTask.budget.payment')}</Note>
    </>
  );
}

const WINDOWS = Object.keys(TIME_WINDOWS) as TimeWindow[];

/** Mockup 03, 3e: today, tomorrow or a day from the calendar, and a time window; or flexible. */
export function WhenStep({
  draft,
  change,
  problems,
  headingRef,
  now,
  locale,
}: StepProps & { now: Date; locale: Locale }) {
  const t = useTranslations();
  const today = zagrebDay(now);
  const tomorrow = addDays(today, 1);
  const [picking, setPicking] = useState(
    draft.dateMode === 'date' &&
      draft.date !== null &&
      draft.date !== today &&
      draft.date !== tomorrow,
  );
  const problem = problemFor(
    problems,
    'postTask.errors.when',
    'postTask.errors.tooFarAhead',
    'postTask.errors.timeWindow',
    'postTask.errors.windowPassed',
  );
  const dated = draft.dateMode === 'date';

  function pickDay(date: string) {
    const keep = draft.timeWindow && windowOpen(date, draft.timeWindow, now);
    change({ dateMode: 'date', date, timeWindow: keep ? draft.timeWindow : null });
  }

  return (
    <>
      <StepHeading headingRef={headingRef}>{t('postTask.when.heading')}</StepHeading>
      <ChoiceGroup legend={t('postTask.when.heading')} hideLegend>
        <Choice
          name="when"
          label={t('postTask.when.today')}
          checked={dated && !picking && draft.date === today}
          onSelect={() => {
            setPicking(false);
            pickDay(today);
          }}
          testId="post-when-today"
        />
        <Choice
          name="when"
          label={t('postTask.when.tomorrow')}
          checked={dated && !picking && draft.date === tomorrow}
          onSelect={() => {
            setPicking(false);
            pickDay(tomorrow);
          }}
          testId="post-when-tomorrow"
        />
        <Choice
          name="when"
          label={t('postTask.when.pickDate')}
          checked={dated && picking}
          onSelect={() => {
            setPicking(true);
            change({ dateMode: 'date' });
          }}
          testId="post-when-pick"
        />
        <Choice
          name="when"
          label={t('postTask.when.flexible')}
          checked={draft.dateMode === 'flexible'}
          onSelect={() => {
            setPicking(false);
            change({ dateMode: 'flexible', date: null, timeWindow: null });
          }}
          testId="post-when-flexible"
        />
      </ChoiceGroup>
      {draft.dateMode === 'flexible' ? (
        <p className={`${styles.small} ${styles.muted}`}>{t('postTask.when.flexibleHint')}</p>
      ) : null}
      {dated && picking ? (
        <Calendar
          selected={draft.date}
          first={today}
          last={lastBookableDay(now)}
          locale={locale}
          onPick={pickDay}
        />
      ) : null}
      {dated && draft.date ? (
        <ChoiceGroup legend={t('postTask.when.windowLabel')}>
          {WINDOWS.map((window) => {
            const range = windowRange(draft.date!, window);
            return (
              <Choice
                key={window}
                name="window"
                label={t(`postTask.when.windows.${window}`, {
                  start: formatTime(range.start, locale),
                  end: formatTime(range.end, locale),
                })}
                checked={draft.timeWindow === window}
                disabled={!windowOpen(draft.date!, window, now)}
                onSelect={() => change({ timeWindow: window })}
                testId={`post-window-${window}`}
              />
            );
          })}
        </ChoiceGroup>
      ) : null}
      {problem ? <ProblemText text={t(problem)} testId="post-when-error" /> : null}
      <Note icon={<InfoIcon />}>{t('postTask.when.limit')}</Note>
    </>
  );
}

function monthOf(day: string): [number, number] {
  const [y, m] = day.split('-').map(Number) as [number, number];
  return [y, m - 1];
}

function Calendar({
  selected,
  first,
  last,
  locale,
  onPick,
}: {
  selected: string | null;
  first: string;
  last: string;
  locale: Locale;
  onPick(day: string): void;
}) {
  const t = useTranslations();
  const [[year, month], setMonth] = useState(monthOf(selected ?? first));
  const shown = `${year}-${String(month + 1).padStart(2, '0')}`;
  const title = new Intl.DateTimeFormat(locale, {
    month: 'long',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(new Date(Date.UTC(year, month, 1)));
  const weekday = new Intl.DateTimeFormat(locale, { weekday: 'short', timeZone: 'UTC' });
  const full = new Intl.DateTimeFormat(locale, { dateStyle: 'full', timeZone: 'UTC' });
  // 1 January 2024 was a Monday; weeks start on Monday in all four languages' countries.
  const weekdays = Array.from({ length: 7 }, (_, i) =>
    weekday.format(new Date(Date.UTC(2024, 0, 1 + i))),
  );
  const lead = (new Date(Date.UTC(year, month, 1)).getUTCDay() + 6) % 7;
  const length = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
  const cells: (string | null)[] = [
    ...Array<null>(lead).fill(null),
    ...Array.from({ length }, (_, i) => `${shown}-${String(i + 1).padStart(2, '0')}`),
  ];
  const step = (by: number) =>
    setMonth(([y, m]) => [y + Math.floor((m + by) / 12), (m + by + 12) % 12]);
  const titleId = useId();

  return (
    <Card className={styles.calendar} data-testid="post-calendar">
      <div className={styles.monthRow}>
        <Button
          variant="ghost"
          size="sm"
          aria-label={t('postTask.when.previousMonth')}
          disabled={shown <= first.slice(0, 7)}
          onClick={() => step(-1)}
          data-testid="post-calendar-previous"
        >
          <span aria-hidden="true">‹</span>
        </Button>
        <p id={titleId} className={styles.label} aria-live="polite">
          {title}
        </p>
        <Button
          variant="ghost"
          size="sm"
          aria-label={t('postTask.when.nextMonth')}
          disabled={shown >= last.slice(0, 7)}
          onClick={() => step(1)}
          data-testid="post-calendar-next"
        >
          <span aria-hidden="true">›</span>
        </Button>
      </div>
      <div className={styles.grid} aria-hidden="true">
        {weekdays.map((name) => (
          <span key={name} className={styles.weekday}>
            {name}
          </span>
        ))}
      </div>
      <ul className={styles.grid} aria-labelledby={titleId}>
        {cells.map((day, index) => {
          if (!day) return <li key={`blank-${index}`} aria-hidden="true" />;
          const open = day >= first && day <= last;
          return (
            <li key={day}>
              <button
                type="button"
                className={styles.day}
                aria-label={full.format(new Date(`${day}T12:00:00Z`))}
                aria-pressed={day === selected}
                disabled={!open}
                onClick={() => onPick(day)}
                data-testid={`post-day-${day}`}
              >
                {Number(day.slice(8))}
              </button>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

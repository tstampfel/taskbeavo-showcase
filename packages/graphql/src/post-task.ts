import { TASK_TIME_ZONE } from '@taskbeavo/i18n';
import type { TaskCategory, TaskInput } from './generated/graphql';

/**
 * The post-a-task wizard's draft and rules (BEAV-86, mockup 03), shared by the mobile and web
 * wizards (BEAV-87). Pure functions, so the screens only render and the rules are tested here. The server re-checks everything
 * (BEAV-85); these checks only spare the poster a round trip.
 */

export const WIZARD_STEPS = ['what', 'details', 'where', 'when', 'budget', 'review'] as const;
export type WizardStep = (typeof WIZARD_STEPS)[number];

/** Steps with a number in the top bar ("2/5"); review comes after them. */
export const NUMBERED_STEPS = 5;

/** The categories, in the order the wizard and the feed list them. */
export const TASK_CATEGORIES: readonly TaskCategory[] = [
  'GARDEN',
  'ERRANDS',
  'REPAIRS',
  'CLEANING',
  'CHECK_IN',
];

/** Mockup 03: "Photos (optional, up to 5)". The API takes up to 10. */
export const MAX_PHOTOS = 5;
/** The API's limits (backend/tasks/services.py). */
export const MAX_TITLE = 100;
export const MAX_DESCRIPTION = 2_000;
export const MAX_ACCESS_DETAILS = 500;
export const MIN_BUDGET_CENTS = 1_000;
export const MAX_BUDGET_CENTS = 1_000_000;
export const MAX_DAYS_AHEAD = 60;

/** Time windows in Zagreb time: [start hour, end hour). */
export const TIME_WINDOWS = {
  MORNING: [8, 12],
  AFTERNOON: [12, 17],
  EVENING: [17, 21],
} as const;
export type TimeWindow = keyof typeof TIME_WINDOWS;

export interface DraftPhoto {
  id: string;
  url: string | null;
}

export interface TaskDraft {
  step: WizardStep;
  category: TaskCategory | null;
  title: string;
  description: string;
  photos: DraftPhoto[];
  /** The address the poster picked from the search, with its real coordinates. */
  address: string;
  lat: number | null;
  lng: number | null;
  /** The town of the picked address, for the waitlist when it's outside the service area. */
  city: string;
  accessDetails: string;
  careContactId: string | null;
  dateMode: 'date' | 'flexible' | null;
  /** A Zagreb calendar day, YYYY-MM-DD. */
  date: string | null;
  timeWindow: TimeWindow | null;
  /**
   * The budget as the poster typed it, in euros. Starts empty and is never filled in by the
   * app: no suggested, default or typical amount (hard rule k).
   */
  budget: string;
}

export function emptyDraft(): TaskDraft {
  return {
    step: 'what',
    category: null,
    title: '',
    description: '',
    photos: [],
    address: '',
    lat: null,
    lng: null,
    city: '',
    accessDetails: '',
    careContactId: null,
    dateMode: null,
    date: null,
    timeWindow: null,
    budget: '',
  };
}

/** Whether the poster entered anything worth resuming. */
export function isDraftEmpty(draft: TaskDraft): boolean {
  const empty = emptyDraft();
  return (Object.keys(empty) as (keyof TaskDraft)[]).every(
    (key) => key === 'step' || JSON.stringify(draft[key]) === JSON.stringify(empty[key]),
  );
}

/** Euros as typed ("30", "30,50", "€ 12") in cents, or null when it isn't an amount. */
export function parseBudgetCents(text: string): number | null {
  const cleaned = text.replace(/€/g, '').trim();
  const match = /^(\d{1,6})(?:[.,](\d{1,2}))?$/.exec(cleaned);
  if (!match) return null;
  const euros = Number(match[1]);
  const cents = Number((match[2] ?? '').padEnd(2, '0'));
  return euros * 100 + cents;
}

const DAY_MS = 24 * 3600 * 1000;

const zagrebParts = new Intl.DateTimeFormat('en-GB', {
  timeZone: TASK_TIME_ZONE,
  hourCycle: 'h23',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
});

function wallClock(instant: number) {
  const parts = zagrebParts.formatToParts(new Date(instant));
  const get = (type: Intl.DateTimeFormatPartTypes) =>
    Number(parts.find((part) => part.type === type)?.value);
  return {
    year: get('year'),
    month: get('month'),
    day: get('day'),
    ms: Date.UTC(
      get('year'),
      get('month') - 1,
      get('day'),
      get('hour'),
      get('minute'),
      get('second'),
    ),
  };
}

const pad = (n: number) => String(n).padStart(2, '0');

/** The Zagreb calendar day of an instant, YYYY-MM-DD. */
export function zagrebDay(instant: Date): string {
  const { year, month, day } = wallClock(instant.getTime());
  return `${year}-${pad(month)}-${pad(day)}`;
}

/** The instant a Zagreb day and hour fall on, in summer and winter time alike. */
export function zagrebInstant(day: string, hour: number): Date {
  const [y, m, d] = day.split('-').map(Number) as [number, number, number];
  const wall = Date.UTC(y, m - 1, d, hour);
  let instant = wall - (wallClock(wall).ms - wall);
  // Near a clock change the first guess's offset can be the other one.
  instant = wall - (wallClock(instant).ms - instant);
  return new Date(instant);
}

export function addDays(day: string, days: number): string {
  const [y, m, d] = day.split('-').map(Number) as [number, number, number];
  const date = new Date(Date.UTC(y, m - 1, d) + days * DAY_MS);
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}`;
}

/** The last day a window can be on, so it still ends within 60 days. */
export function lastBookableDay(now: Date): string {
  return addDays(zagrebDay(now), MAX_DAYS_AHEAD - 1);
}

export function windowRange(day: string, window: TimeWindow): { start: Date; end: Date } {
  const [startHour, endHour] = TIME_WINDOWS[window];
  return { start: zagrebInstant(day, startHour), end: zagrebInstant(day, endHour) };
}

/** Whether a window on that day can still be picked: it hasn't started yet. */
export function windowOpen(day: string, window: TimeWindow, now: Date): boolean {
  return windowRange(day, window).start.getTime() > now.getTime();
}

/**
 * What stops the poster leaving a step, as catalogue keys (postTask.errors.*, or the API's
 * own errors.* message where it says the same). Empty when the step is complete.
 */
export function stepProblems(step: WizardStep, draft: TaskDraft, now: Date): string[] {
  const problems: string[] = [];
  switch (step) {
    case 'what':
      if (!draft.category) problems.push('postTask.errors.category');
      if (!draft.title.trim()) problems.push('postTask.errors.title');
      break;
    case 'details':
      if (!draft.description.trim()) problems.push('postTask.errors.description');
      break;
    case 'where':
      if (draft.lat === null || draft.lng === null || !draft.address.trim()) {
        problems.push('postTask.errors.address');
      }
      break;
    case 'when':
      if (draft.dateMode === 'flexible') break;
      if (draft.dateMode !== 'date' || !draft.date) {
        problems.push('postTask.errors.when');
      } else if (draft.date > lastBookableDay(now)) {
        problems.push('postTask.errors.tooFarAhead');
      } else if (!draft.timeWindow) {
        problems.push('postTask.errors.timeWindow');
      } else if (!windowOpen(draft.date, draft.timeWindow, now)) {
        problems.push('postTask.errors.windowPassed');
      }
      break;
    case 'budget': {
      const cents = parseBudgetCents(draft.budget);
      if (cents === null) problems.push('postTask.errors.budget');
      else if (cents < MIN_BUDGET_CENTS) problems.push('errors.BUDGET_BELOW_MINIMUM');
      else if (cents > MAX_BUDGET_CENTS) problems.push('postTask.errors.budgetTooHigh');
      break;
    }
    case 'review':
      break;
  }
  return problems;
}

/** The first step with a problem, or null when the draft can be posted. */
export function firstIncompleteStep(draft: TaskDraft, now: Date): WizardStep | null {
  return WIZARD_STEPS.find((step) => stepProblems(step, draft, now).length > 0) ?? null;
}

/** The postTask input, or null while a step has a problem. */
export function toTaskInput(draft: TaskDraft, now: Date): TaskInput | null {
  if (firstIncompleteStep(draft, now) !== null) return null;
  const range =
    draft.dateMode === 'date' && draft.date && draft.timeWindow
      ? windowRange(draft.date, draft.timeWindow)
      : null;
  const accessDetails = draft.accessDetails.trim();
  return {
    category: draft.category,
    title: draft.title.trim(),
    description: draft.description.trim(),
    location: { address: draft.address.trim(), lat: draft.lat!, lng: draft.lng! },
    ...(accessDetails ? { accessDetails } : {}),
    ...(draft.careContactId ? { careContactId: draft.careContactId } : {}),
    flexibleDate: range === null,
    windowStart: range?.start.toISOString() ?? null,
    windowEnd: range?.end.toISOString() ?? null,
    budgetCents: parseBudgetCents(draft.budget),
    photoIds: draft.photos.map((photo) => photo.id),
  };
}

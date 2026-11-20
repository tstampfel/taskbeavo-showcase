import {
  TASK_CATEGORIES,
  TIME_WINDOWS,
  WIZARD_STEPS,
  emptyDraft,
  type TaskDraft,
} from '@taskbeavo/graphql';

/** `window.localStorage` and `window.sessionStorage` fit this. */
export type DraftStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

export interface TaskDraftStore {
  /**
   * The draft `owner` (the signed-in viewer's id) saved, or null when there is none, it can't be
   * read, or someone else saved it; another account's draft is deleted.
   */
  load(owner: string): TaskDraft | null;
  save(draft: TaskDraft, owner: string): void;
  /** Deletes both parts: after posting, on "Start again" and on sign-out. */
  clear(): void;
}

export const DRAFT_KEY = 'taskbeavo.taskDraft';
export const DRAFT_PLACE_KEY = 'taskbeavo.taskDraftPlace';

/**
 * The exact place (hard rule e) stays in sessionStorage, so it ends with the tab; the rest of
 * the draft is in localStorage, so the wizard can offer to resume it. The access details are
 * never stored, and the on-site contact's phone isn't in the draft at all (only the saved
 * contact's id is). Both parts carry the draft's random id, and the place joins the draft only
 * when the ids match: a tab's place never lands in a draft another tab or account started
 * after it (review R1-1). The draft also names its owner, so the next account to sign in on
 * this browser never sees it (R1-2).
 */
const PLACE_FIELDS = ['address', 'lat', 'lng', 'city'] as const;

function parse(text: string | null): Record<string, unknown> | null {
  if (!text) return null;
  try {
    const value: unknown = JSON.parse(text);
    return value && typeof value === 'object' && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

const DATE = /^\d{4}-\d{2}-\d{2}$/;

const oneOf = <T extends string>(options: readonly T[], value: unknown): T | null =>
  typeof value === 'string' && (options as readonly string[]).includes(value) ? (value as T) : null;

/**
 * Takes only known fields with valid values, so an old or damaged draft starts that field
 * fresh instead of breaking the wizard (review R1-3).
 */
function fromSaved(saved: Record<string, unknown>): TaskDraft {
  const draft = emptyDraft();
  const text = (value: unknown) => (typeof value === 'string' ? value : '');
  draft.step = oneOf(WIZARD_STEPS, saved.step) ?? 'what';
  draft.category = oneOf(TASK_CATEGORIES, saved.category);
  draft.title = text(saved.title);
  draft.description = text(saved.description);
  draft.photos = Array.isArray(saved.photos)
    ? saved.photos.flatMap((photo: unknown) =>
        photo && typeof photo === 'object' && typeof (photo as { id?: unknown }).id === 'string'
          ? [{ id: (photo as { id: string }).id, url: null }]
          : [],
      )
    : [];
  draft.address = text(saved.address);
  draft.city = text(saved.city);
  if (typeof saved.lat === 'number' && typeof saved.lng === 'number') {
    draft.lat = saved.lat;
    draft.lng = saved.lng;
  }
  draft.careContactId = typeof saved.careContactId === 'string' ? saved.careContactId : null;
  draft.dateMode = oneOf(['date', 'flexible'] as const, saved.dateMode);
  draft.date = typeof saved.date === 'string' && DATE.test(saved.date) ? saved.date : null;
  draft.timeWindow = oneOf(
    Object.keys(TIME_WINDOWS) as (keyof typeof TIME_WINDOWS)[],
    saved.timeWindow,
  );
  draft.budget = text(saved.budget);
  return draft;
}

function newDraftId(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

/** Runs a storage call; a blocked or missing storage (private mode, the server) does nothing. */
function attempt<T>(run: () => T): T | null {
  try {
    return run();
  } catch {
    return null;
  }
}

export function createTaskDraftStore(
  local: () => DraftStorage | null,
  session: () => DraftStorage | null,
): TaskDraftStore {
  const clear = () => {
    // Each part on its own, so one failing still removes the other.
    attempt(() => session()?.removeItem(DRAFT_PLACE_KEY));
    attempt(() => local()?.removeItem(DRAFT_KEY));
  };
  const readOpen = () => parse(attempt(() => local()?.getItem(DRAFT_KEY) ?? null));

  return {
    load(owner) {
      const saved = readOpen();
      if (!saved) return null;
      if (saved.owner !== owner || typeof saved.draftId !== 'string') {
        clear();
        return null;
      }
      const place = parse(attempt(() => session()?.getItem(DRAFT_PLACE_KEY) ?? null));
      if (place && place.draftId !== saved.draftId) {
        // Another draft's place: never joined to this one.
        attempt(() => session()?.removeItem(DRAFT_PLACE_KEY));
        return fromSaved(saved);
      }
      return fromSaved({ ...saved, ...(place ?? {}) });
    },
    save(draft, owner) {
      const current = readOpen();
      const draftId =
        current?.owner === owner && typeof current.draftId === 'string'
          ? current.draftId
          : newDraftId();
      // Photo URLs are signed for minutes only: keep the ids.
      const open: Record<string, unknown> = {
        ...draft,
        photos: draft.photos.map((photo) => ({ id: photo.id, url: null })),
        draftId,
        owner,
      };
      delete open.accessDetails;
      const place: Record<string, unknown> = { draftId };
      for (const key of PLACE_FIELDS) {
        place[key] = draft[key];
        delete open[key];
      }
      attempt(() => session()?.setItem(DRAFT_PLACE_KEY, JSON.stringify(place)));
      attempt(() => local()?.setItem(DRAFT_KEY, JSON.stringify(open)));
    },
    clear,
  };
}

/** The draft in this browser. Safe to call on the server, where there is no storage. */
export const browserTaskDraftStore = createTaskDraftStore(
  () => (typeof window === 'undefined' ? null : window.localStorage),
  () => (typeof window === 'undefined' ? null : window.sessionStorage),
);

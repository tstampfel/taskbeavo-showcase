import { emptyDraft, WIZARD_STEPS, type TaskDraft } from '@taskbeavo/graphql';

/** `window.localStorage` and `window.sessionStorage` fit this. */
export type DraftStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

export interface TaskDraftStore {
  /** The saved draft, or null when there is none or it can't be read. */
  load(): TaskDraft | null;
  save(draft: TaskDraft): void;
  /** Deletes both parts: after posting, on "Start again" and on sign-out. */
  clear(): void;
}

export const DRAFT_KEY = 'taskbeavo.taskDraft';
export const DRAFT_PLACE_KEY = 'taskbeavo.taskDraftPlace';

/**
 * The exact place (hard rule e) stays in sessionStorage, so it ends with the tab; the rest of
 * the draft is in localStorage, so the wizard can offer to resume it. The access details are
 * never stored, and the on-site contact's phone isn't in the draft at all (only the saved
 * contact's id is).
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

/** Takes only known fields of the right type, so an old or damaged draft can't break the wizard. */
function fromSaved(saved: Record<string, unknown>): TaskDraft {
  const draft = emptyDraft();
  for (const key of Object.keys(draft) as (keyof TaskDraft)[]) {
    const value = saved[key];
    const fallback = draft[key];
    const fits =
      fallback === null
        ? value === null || typeof value === 'string' || typeof value === 'number'
        : typeof value === typeof fallback && Array.isArray(value) === Array.isArray(fallback);
    if (fits) (draft as unknown as Record<string, unknown>)[key] = value;
  }
  if (!(WIZARD_STEPS as readonly string[]).includes(draft.step)) draft.step = 'what';
  draft.photos = draft.photos.filter(
    (photo) => photo && typeof photo === 'object' && typeof photo.id === 'string',
  );
  if (typeof draft.lat !== 'number' || typeof draft.lng !== 'number') {
    draft.lat = null;
    draft.lng = null;
  }
  draft.accessDetails = '';
  return draft;
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
  return {
    load() {
      const saved = parse(attempt(() => local()?.getItem(DRAFT_KEY) ?? null));
      if (!saved) return null;
      const place = parse(attempt(() => session()?.getItem(DRAFT_PLACE_KEY) ?? null));
      return fromSaved({ ...saved, ...(place ?? {}) });
    },
    save(draft) {
      // Photo URLs are signed for minutes only: keep the ids.
      const open: Record<string, unknown> = {
        ...draft,
        photos: draft.photos.map((photo) => ({ id: photo.id, url: null })),
      };
      delete open.accessDetails;
      const place: Record<string, unknown> = {};
      for (const key of PLACE_FIELDS) {
        place[key] = draft[key];
        delete open[key];
      }
      attempt(() => session()?.setItem(DRAFT_PLACE_KEY, JSON.stringify(place)));
      attempt(() => local()?.setItem(DRAFT_KEY, JSON.stringify(open)));
    },
    clear() {
      // Each part on its own, so one failing still removes the other.
      attempt(() => session()?.removeItem(DRAFT_PLACE_KEY));
      attempt(() => local()?.removeItem(DRAFT_KEY));
    },
  };
}

/** The draft in this browser. Safe to call on the server, where there is no storage. */
export const browserTaskDraftStore = createTaskDraftStore(
  () => (typeof window === 'undefined' ? null : window.localStorage),
  () => (typeof window === 'undefined' ? null : window.sessionStorage),
);

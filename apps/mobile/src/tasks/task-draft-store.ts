import { emptyDraft, WIZARD_STEPS, type TaskDraft } from './post-task-model';

/** AsyncStorage and the SecureStore adapter below both fit this. */
export interface KeyValueStore {
  getItem(key: string): Promise<string | null>;
  setItem(key: string, value: string): Promise<void>;
  removeItem(key: string): Promise<void>;
}

export interface TaskDraftStore {
  /** The saved draft, or null when there is none or it can't be read. */
  load(): Promise<TaskDraft | null>;
  save(draft: TaskDraft): Promise<void>;
  /** Deletes both parts; the wizard calls it after posting and on discard, the reset on logout. */
  clear(): Promise<void>;
}

export const DRAFT_KEY = 'taskbeavo.taskDraft';
export const DRAFT_PRIVATE_KEY = 'taskbeavo.taskDraftPrivate';

/**
 * The exact place and the access details (hard rule e): kept encrypted in SecureStore, never in
 * AsyncStorage, as the persisted Apollo cache leaves them out too. The on-site contact's phone
 * isn't in the draft at all: the draft keeps only the saved contact's id.
 */
const PRIVATE_FIELDS = ['address', 'lat', 'lng', 'city', 'accessDetails'] as const;
type PrivateField = (typeof PRIVATE_FIELDS)[number];

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
  return draft;
}

export function createTaskDraftStore(
  storage: KeyValueStore,
  secureStorage: KeyValueStore,
): TaskDraftStore {
  return {
    async load() {
      try {
        const saved = parse(await storage.getItem(DRAFT_KEY));
        if (!saved) return null;
        const secret = parse(await secureStorage.getItem(DRAFT_PRIVATE_KEY).catch(() => null));
        return fromSaved({ ...saved, ...(secret ?? {}) });
      } catch {
        return null;
      }
    },
    async save(draft) {
      // Photo URLs are signed for minutes only (and Upload.url is never persisted): keep ids.
      const open: Record<string, unknown> = {
        ...draft,
        photos: draft.photos.map((photo) => ({ id: photo.id, url: null })),
      };
      const secret: Partial<Pick<TaskDraft, PrivateField>> = {};
      for (const key of PRIVATE_FIELDS) {
        (secret as Record<string, unknown>)[key] = draft[key];
        delete open[key];
      }
      await secureStorage.setItem(DRAFT_PRIVATE_KEY, JSON.stringify(secret));
      await storage.setItem(DRAFT_KEY, JSON.stringify(open));
    },
    async clear() {
      // Each part on its own, so one failing still removes the other.
      await Promise.allSettled([
        secureStorage.removeItem(DRAFT_PRIVATE_KEY),
        storage.removeItem(DRAFT_KEY),
      ]);
    },
  };
}

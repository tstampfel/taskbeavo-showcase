import { describe, expect, it } from 'vitest';
import { emptyDraft, type TaskDraft } from '@taskbeavo/graphql';
import {
  DRAFT_KEY,
  DRAFT_PLACE_KEY,
  createTaskDraftStore,
  type DraftStorage,
} from './task-draft-store';

function memory(): DraftStorage & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return {
    data,
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => {
      data.set(key, value);
    },
    removeItem: (key) => {
      data.delete(key);
    },
  };
}

function broken(): DraftStorage {
  const fail = () => {
    throw new Error('SecurityError: storage is blocked');
  };
  return { getItem: fail, setItem: fail, removeItem: fail };
}

const draft: TaskDraft = {
  ...emptyDraft(),
  step: 'where',
  category: 'GARDEN',
  title: 'Mow the lawn',
  description: 'Front and back.',
  photos: [{ id: 'upload-1', url: null }],
  address: 'Ulica Ante Starčevića 1, Dubrovnik',
  lat: 42.66,
  lng: 18.08,
  city: 'Dubrovnik',
  accessDetails: 'Gate code 4417',
  careContactId: 'contact-1',
  budget: '30',
};

describe('the browser draft (BEAV-87)', () => {
  it('never stores the access details anywhere (hard rule e)', () => {
    const local = memory();
    const session = memory();
    createTaskDraftStore(
      () => local,
      () => session,
    ).save(draft);

    for (const stored of [...local.data.values(), ...session.data.values()]) {
      expect(stored).not.toContain('4417');
    }
  });

  it('keeps the exact place in this tab only, out of lasting storage', () => {
    const local = memory();
    const session = memory();
    const store = createTaskDraftStore(
      () => local,
      () => session,
    );

    store.save(draft);

    const open = local.data.get(DRAFT_KEY) ?? '';
    expect(open).toContain('Mow the lawn');
    for (const place of ['Starčevića', '42.66', '18.08', 'Dubrovnik']) {
      expect(open).not.toContain(place);
    }
    expect(session.data.get(DRAFT_PLACE_KEY)).toContain('Starčevića');
    expect(store.load()).toEqual({ ...draft, accessDetails: '' });
  });

  it('keeps photo ids but not their short-lived signed URLs', () => {
    const local = memory();
    const store = createTaskDraftStore(() => local, memory);
    store.save({ ...draft, photos: [{ id: 'upload-2', url: 'https://r2.example.com/x?sig=1' }] });
    expect(local.data.get(DRAFT_KEY)).not.toContain('sig=1');
    expect(store.load()?.photos).toEqual([{ id: 'upload-2', url: null }]);
  });

  it('clears both parts, even when one storage throws', () => {
    const local = memory();
    const session = memory();
    const store = createTaskDraftStore(
      () => local,
      () => session,
    );
    store.save(draft);
    session.removeItem = () => {
      throw new Error('blocked');
    };

    store.clear();

    expect(local.data.size).toBe(0);
  });

  it('reads a damaged or old draft as far as it can', () => {
    const local = memory();
    const store = createTaskDraftStore(() => local, memory);

    local.data.set(DRAFT_KEY, '{not json');
    expect(store.load()).toBeNull();

    local.data.set(DRAFT_KEY, JSON.stringify({ step: 'gone', title: 7, budget: '12', extra: 1 }));
    expect(store.load()).toEqual({ ...emptyDraft(), budget: '12' });
  });

  it('drops the address when the tab that had it is gone', () => {
    const local = memory();
    createTaskDraftStore(() => local, memory).save(draft);

    const loaded = createTaskDraftStore(() => local, memory).load();

    expect(loaded).toMatchObject({ title: 'Mow the lawn', address: '', lat: null, lng: null });
  });

  it('works without storage: blocked, private mode or on the server', () => {
    const store = createTaskDraftStore(broken, () => null);
    expect(() => store.save(draft)).not.toThrow();
    expect(store.load()).toBeNull();
    expect(() => store.clear()).not.toThrow();
  });
});

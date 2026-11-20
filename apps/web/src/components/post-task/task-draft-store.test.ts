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

const ANA = 'user-ana';
const BORIS = 'user-boris';

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
    ).save(draft, ANA);

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

    store.save(draft, ANA);

    const open = local.data.get(DRAFT_KEY) ?? '';
    expect(open).toContain('Mow the lawn');
    for (const place of ['Starčevića', '42.66', '18.08', 'Dubrovnik']) {
      expect(open).not.toContain(place);
    }
    expect(session.data.get(DRAFT_PLACE_KEY)).toContain('Starčevića');
    expect(store.load(ANA)).toEqual({ ...draft, accessDetails: '' });
  });

  it('keeps photo ids but not their short-lived signed URLs', () => {
    const local = memory();
    const store = createTaskDraftStore(() => local, memory);
    store.save(
      { ...draft, photos: [{ id: 'upload-2', url: 'https://r2.example.com/x?sig=1' }] },
      ANA,
    );
    expect(local.data.get(DRAFT_KEY)).not.toContain('sig=1');
    expect(store.load(ANA)?.photos).toEqual([{ id: 'upload-2', url: null }]);
  });

  it('clears both parts, even when one storage throws', () => {
    const local = memory();
    const session = memory();
    const store = createTaskDraftStore(
      () => local,
      () => session,
    );
    store.save(draft, ANA);
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
    expect(store.load(ANA)).toBeNull();

    local.data.set(
      DRAFT_KEY,
      JSON.stringify({ owner: ANA, draftId: 'd1', step: 'gone', title: 7, budget: '12', extra: 1 }),
    );
    expect(store.load(ANA)).toEqual({ ...emptyDraft(), budget: '12' });
  });

  it('drops the address when the tab that had it is gone', () => {
    const local = memory();
    createTaskDraftStore(() => local, memory).save(draft, ANA);

    const loaded = createTaskDraftStore(() => local, memory).load(ANA);

    expect(loaded).toMatchObject({ title: 'Mow the lawn', address: '', lat: null, lng: null });
  });

  it('works without storage: blocked, private mode or on the server', () => {
    const store = createTaskDraftStore(broken, () => null);
    expect(() => store.save(draft, ANA)).not.toThrow();
    expect(store.load(ANA)).toBeNull();
    expect(() => store.clear()).not.toThrow();
  });

  it('never joins a tab’s place to a draft another tab started later (R1-1)', () => {
    const local = memory();
    const tab1 = memory();
    const tab2 = memory();
    // Tab 1 picks an address; tab 2 then posts (clearing the shared part) and starts again.
    createTaskDraftStore(
      () => local,
      () => tab1,
    ).save(draft, ANA);
    const second = createTaskDraftStore(
      () => local,
      () => tab2,
    );
    second.clear();
    second.save({ ...emptyDraft(), title: 'Wash the car' }, ANA);

    const reloaded = createTaskDraftStore(
      () => local,
      () => tab1,
    ).load(ANA);

    expect(reloaded).toMatchObject({ title: 'Wash the car', address: '', lat: null, lng: null });
    expect(tab1.data.size).toBe(0);
  });

  it('never gives one account’s address to another account’s draft (R1-1)', () => {
    const local = memory();
    const tab1 = memory();
    const tab2 = memory();
    createTaskDraftStore(
      () => local,
      () => tab1,
    ).save(draft, ANA);
    // Ana signs out in tab 2; Boris signs in there and starts his own draft.
    const other = createTaskDraftStore(
      () => local,
      () => tab2,
    );
    other.clear();
    other.save({ ...emptyDraft(), title: 'Boris’s task' }, BORIS);

    const loaded = createTaskDraftStore(
      () => local,
      () => tab1,
    ).load(BORIS);

    expect(loaded).toMatchObject({ title: 'Boris’s task', address: '', lat: null, lng: null });
    expect(JSON.stringify(loaded)).not.toContain('Starčevića');
  });

  it('ignores a place saved without a draft id', () => {
    const local = memory();
    const session = memory();
    const store = createTaskDraftStore(
      () => local,
      () => session,
    );
    store.save({ ...emptyDraft(), title: 'Mow the lawn' }, ANA);
    session.data.set(DRAFT_PLACE_KEY, JSON.stringify({ address: 'Old street 1', lat: 1, lng: 2 }));

    expect(store.load(ANA)).toMatchObject({ address: '', lat: null, lng: null });
  });

  it('offers a draft only to the account that wrote it, and deletes it for anyone else (R1-2)', () => {
    const local = memory();
    const store = createTaskDraftStore(() => local, memory);
    store.save(draft, ANA);

    expect(store.load(BORIS)).toBeNull();
    expect(local.data.size).toBe(0);
    expect(store.load(ANA)).toBeNull();
  });

  it('starts a damaged field fresh instead of breaking the wizard (R1-3)', () => {
    const local = memory();
    const store = createTaskDraftStore(() => local, memory);
    local.data.set(
      DRAFT_KEY,
      JSON.stringify({
        owner: ANA,
        draftId: 'd1',
        step: 'review',
        category: 'PETS',
        title: 'Mow the lawn',
        dateMode: 'sometime',
        date: 5,
        timeWindow: 'NIGHT',
        careContactId: 7,
        photos: [{ id: 'upload-1' }, { id: 3 }, null],
      }),
    );

    expect(store.load(ANA)).toEqual({
      ...emptyDraft(),
      step: 'review',
      title: 'Mow the lawn',
      photos: [{ id: 'upload-1', url: null }],
    });

    local.data.set(
      DRAFT_KEY,
      JSON.stringify({ owner: ANA, draftId: 'd1', dateMode: 'date', date: '2026-11-3x' }),
    );
    expect(store.load(ANA)?.date).toBeNull();
  });
});

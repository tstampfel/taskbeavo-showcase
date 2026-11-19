import { emptyDraft, type TaskDraft } from '../src/tasks/post-task-model';
import {
  DRAFT_KEY,
  DRAFT_PRIVATE_KEY,
  createTaskDraftStore,
  type KeyValueStore,
} from '../src/tasks/task-draft-store';

function memory(): KeyValueStore & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return {
    data,
    getItem: async (key) => data.get(key) ?? null,
    setItem: async (key, value) => {
      data.set(key, value);
    },
    removeItem: async (key) => {
      data.delete(key);
    },
  };
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

it('keeps the exact place and access details out of plain storage (hard rule e)', async () => {
  const plain = memory();
  const secure = memory();
  const store = createTaskDraftStore(plain, secure);

  await store.save(draft);

  const open = plain.data.get(DRAFT_KEY) ?? '';
  expect(open).toContain('Mow the lawn');
  for (const secret of ['Starčevića', '42.66', '18.08', 'Dubrovnik', '4417']) {
    expect(open).not.toContain(secret);
  }
  expect(secure.data.get(DRAFT_PRIVATE_KEY)).toContain('4417');
  expect(await store.load()).toEqual(draft);
});

it('keeps photo ids but not their short-lived signed URLs', async () => {
  const plain = memory();
  const store = createTaskDraftStore(plain, memory());
  await store.save({
    ...draft,
    photos: [{ id: 'upload-2', url: 'https://r2.example.com/x?sig=1' }],
  });
  expect(plain.data.get(DRAFT_KEY)).not.toContain('sig=1');
  expect((await store.load())?.photos).toEqual([{ id: 'upload-2', url: null }]);
});

it('clears both parts, even when one fails', async () => {
  const plain = memory();
  const secure = memory();
  const store = createTaskDraftStore(plain, secure);
  await store.save(draft);
  secure.removeItem = () => Promise.reject(new Error('locked'));

  await store.clear();

  expect(plain.data.size).toBe(0);
  expect(await createTaskDraftStore(plain, memory()).load()).toBeNull();
});

it('reads a damaged or old draft as far as it can', async () => {
  const plain = memory();
  const store = createTaskDraftStore(plain, memory());

  plain.data.set(DRAFT_KEY, '{not json');
  expect(await store.load()).toBeNull();

  plain.data.set(DRAFT_KEY, JSON.stringify({ step: 'gone', title: 7, budget: '12', extra: 1 }));
  expect(await store.load()).toEqual({ ...emptyDraft(), budget: '12' });
});

it('drops coordinates when the secure part is missing', async () => {
  const plain = memory();
  const store = createTaskDraftStore(plain, memory());
  await store.save(draft);

  const loaded = await createTaskDraftStore(plain, memory()).load();

  expect(loaded).toMatchObject({ title: 'Mow the lawn', address: '', lat: null, lng: null });
});

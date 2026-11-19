import {
  MAX_PHOTOS,
  addDays,
  emptyDraft,
  isDraftEmpty,
  lastBookableDay,
  parseBudgetCents,
  stepProblems,
  toTaskInput,
  windowRange,
  zagrebDay,
  zagrebInstant,
  type TaskDraft,
} from '../src/tasks/post-task-model';

// Thursday 19 November 2026, 10:00 in Zagreb (CET, UTC+1).
const NOW = new Date('2026-11-19T09:00:00Z');

function complete(extra: Partial<TaskDraft> = {}): TaskDraft {
  return {
    ...emptyDraft(),
    category: 'ERRANDS',
    title: ' Grocery run for my mum ',
    description: 'Bread, milk and fruit from Gruž market.',
    photos: [{ id: 'upload-1', url: 'https://r2.example.com/1.jpg' }],
    address: 'Ulica Ante Starčevića 1, Dubrovnik',
    lat: 42.66,
    lng: 18.08,
    city: 'Dubrovnik',
    accessDetails: 'Ring twice',
    careContactId: 'contact-1',
    dateMode: 'date',
    date: '2026-11-21',
    timeWindow: 'MORNING',
    budget: '30',
    ...extra,
  };
}

describe('budget', () => {
  it('starts empty: the app never suggests a price (hard rule k)', () => {
    expect(emptyDraft().budget).toBe('');
    expect(stepProblems('budget', emptyDraft(), NOW)).toEqual(['postTask.errors.budget']);
  });

  it.each([
    ['30', 3_000],
    ['30,50', 3_050],
    ['30.5', 3_050],
    [' € 12 ', 1_200],
    ['10000', 1_000_000],
  ])('reads %p as %p cents', (text, cents) => {
    expect(parseBudgetCents(text)).toBe(cents);
  });

  it.each(['', 'abc', '30,505', '-5', '1e3', '12 34'])('refuses %p', (text) => {
    expect(parseBudgetCents(text)).toBeNull();
  });

  it('needs at least €10 and at most €10,000', () => {
    expect(stepProblems('budget', complete({ budget: '9,99' }), NOW)).toEqual([
      'errors.BUDGET_BELOW_MINIMUM',
    ]);
    expect(stepProblems('budget', complete({ budget: '10' }), NOW)).toEqual([]);
    expect(stepProblems('budget', complete({ budget: '10000,01' }), NOW)).toEqual([
      'postTask.errors.budgetTooHigh',
    ]);
  });
});

describe('Zagreb time', () => {
  it('turns a Zagreb day and hour into the instant, in winter and summer time', () => {
    expect(zagrebInstant('2026-11-21', 8).toISOString()).toBe('2026-11-21T07:00:00.000Z');
    expect(zagrebInstant('2026-07-01', 8).toISOString()).toBe('2026-07-01T06:00:00.000Z');
  });

  it('knows the Zagreb day, also just after midnight there', () => {
    expect(zagrebDay(NOW)).toBe('2026-11-19');
    expect(zagrebDay(new Date('2026-11-19T23:30:00Z'))).toBe('2026-11-20');
  });

  it('adds days across month ends', () => {
    expect(addDays('2026-11-29', 3)).toBe('2026-12-02');
  });

  it('lets the last window end within 60 days', () => {
    const last = lastBookableDay(NOW);
    expect(last).toBe('2027-01-17');
    const { end } = windowRange(last, 'EVENING');
    expect(end.getTime()).toBeLessThanOrEqual(NOW.getTime() + 60 * 24 * 3600 * 1000);
  });
});

describe('steps', () => {
  it('asks for a category and a title first', () => {
    expect(stepProblems('what', emptyDraft(), NOW)).toEqual([
      'postTask.errors.category',
      'postTask.errors.title',
    ]);
    expect(stepProblems('what', complete(), NOW)).toEqual([]);
  });

  it('asks for a description; photos are optional, up to five', () => {
    expect(stepProblems('details', complete({ description: '  ' }), NOW)).toEqual([
      'postTask.errors.description',
    ]);
    expect(stepProblems('details', complete({ photos: [] }), NOW)).toEqual([]);
    expect(MAX_PHOTOS).toBe(5);
  });

  it('needs an address picked from the search, with its coordinates', () => {
    expect(stepProblems('where', complete({ lat: null, lng: null }), NOW)).toEqual([
      'postTask.errors.address',
    ]);
  });

  it('refuses a window that has started or a day past the 60-day limit', () => {
    expect(stepProblems('when', emptyDraft(), NOW)).toEqual(['postTask.errors.when']);
    expect(
      stepProblems('when', complete({ date: '2026-11-19', timeWindow: 'MORNING' }), NOW),
    ).toEqual(['postTask.errors.windowPassed']);
    expect(
      stepProblems('when', complete({ date: '2026-11-19', timeWindow: 'AFTERNOON' }), NOW),
    ).toEqual([]);
    expect(stepProblems('when', complete({ date: '2027-01-18' }), NOW)).toEqual([
      'postTask.errors.tooFarAhead',
    ]);
    expect(stepProblems('when', complete({ timeWindow: null }), NOW)).toEqual([
      'postTask.errors.timeWindow',
    ]);
    expect(stepProblems('when', complete({ dateMode: 'flexible', date: null }), NOW)).toEqual([]);
  });
});

describe('toTaskInput', () => {
  it('builds the postTask input with the poster’s own budget', () => {
    expect(toTaskInput(complete(), NOW)).toEqual({
      category: 'ERRANDS',
      title: 'Grocery run for my mum',
      description: 'Bread, milk and fruit from Gruž market.',
      location: { address: 'Ulica Ante Starčevića 1, Dubrovnik', lat: 42.66, lng: 18.08 },
      accessDetails: 'Ring twice',
      careContactId: 'contact-1',
      flexibleDate: false,
      windowStart: '2026-11-21T07:00:00.000Z',
      windowEnd: '2026-11-21T11:00:00.000Z',
      budgetCents: 3_000,
      photoIds: ['upload-1'],
    });
  });

  it('sends a flexible date without a window and leaves out what is empty', () => {
    const input = toTaskInput(
      complete({ dateMode: 'flexible', careContactId: null, accessDetails: ' ' }),
      NOW,
    );
    expect(input).toMatchObject({ flexibleDate: true, windowStart: null, windowEnd: null });
    expect(input).not.toHaveProperty('careContactId');
    expect(input).not.toHaveProperty('accessDetails');
  });

  it('is null while any step has a problem', () => {
    expect(toTaskInput(complete({ budget: '' }), NOW)).toBeNull();
  });
});

it('treats a draft as empty until the poster entered something', () => {
  expect(isDraftEmpty(emptyDraft())).toBe(true);
  expect(isDraftEmpty({ ...emptyDraft(), step: 'details' })).toBe(true);
  expect(isDraftEmpty({ ...emptyDraft(), title: 'x' })).toBe(false);
});

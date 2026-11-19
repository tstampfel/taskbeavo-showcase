import { InMemoryCache } from '@apollo/client';
import { describe, expect, it } from 'vitest';
import { TaskFeedDocument, type TaskFeedQuery } from './generated/graphql';
import { mergeListingPages, nextPageVariables } from './task-listings';
import { typePolicies } from './typePolicies';

type Item = TaskFeedQuery['taskFeed']['items'][number];

function item(id: string): Item & { __typename: 'TaskListing' } {
  return {
    __typename: 'TaskListing',
    id,
    category: 'GARDEN',
    title: `Task ${id}`,
    description: '',
    approximateLat: 42.65,
    approximateLng: 18.09,
    serviceAreaName: 'Dubrovnik',
    windowStart: null,
    windowEnd: null,
    flexibleDate: true,
    budgetCents: 3_000,
    photos: [],
    isMine: false,
    publishedAt: '2026-11-19T10:00:00Z',
  };
}

function page(ids: string[], endCursor: string | null, hasMore: boolean) {
  return {
    __typename: 'TaskListingPage' as const,
    id: `feed::${endCursor ?? ''}`,
    endCursor,
    hasMore,
    items: ids.map(item),
  };
}

describe('mergeListingPages', () => {
  it('appends a next page and skips tasks already listed', () => {
    const merged = mergeListingPages(
      page(['a', 'b'], 'c1', true),
      page(['b', 'c'], 'c2', false),
      'c1',
    );
    expect(merged.items.map((i) => i.id)).toEqual(['a', 'b', 'c']);
    expect(merged.endCursor).toBe('c2');
    expect(merged.hasMore).toBe(false);
  });

  it('starts again on a first page (a refetch)', () => {
    const merged = mergeListingPages(page(['a', 'b'], 'c1', true), page(['z'], 'c9', false), null);
    expect(merged.items.map((i) => i.id)).toEqual(['z']);
  });
});

describe('nextPageVariables', () => {
  it('asks for the page after the end cursor while there is more', () => {
    expect(nextPageVariables(page(['a'], 'c1', true))).toEqual({ after: 'c1' });
    expect(nextPageVariables(page(['a'], 'c1', false))).toBeNull();
    expect(nextPageVariables(undefined)).toBeNull();
  });
});

describe('the cache', () => {
  it('keeps one growing feed per category', () => {
    const cache = new InMemoryCache({ typePolicies });
    cache.writeQuery({
      query: TaskFeedDocument,
      variables: { first: 2 },
      data: { taskFeed: page(['a', 'b'], 'c1', true) },
    });
    cache.writeQuery({
      query: TaskFeedDocument,
      variables: { first: 2, after: 'c1' },
      data: { taskFeed: page(['c'], null, false) },
    });

    const feed = cache.readQuery({ query: TaskFeedDocument, variables: { first: 2 } });
    expect(feed?.taskFeed.items.map((i) => i.id)).toEqual(['a', 'b', 'c']);
    expect(feed?.taskFeed.hasMore).toBe(false);

    const garden = cache.readQuery({
      query: TaskFeedDocument,
      variables: { first: 2, category: 'GARDEN' },
    });
    expect(garden).toBeNull();
  });
});

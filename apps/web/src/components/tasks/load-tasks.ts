import {
  OpenTasksDocument,
  TaskListingDocument,
  apiErrorCode,
  type TaskCategory,
} from '@taskbeavo/graphql';
import { connection } from 'next/server';
import { query } from '../../apollo/rsc';
import type { Listing } from './listing-text';

/**
 * A public task page's data, or null when the task isn't open (TASK_NOT_FOUND). Fetched per
 * request without the visitor's session, so nothing personal reaches a cached server render,
 * and `TaskListing` has no exact address or access details at all (hard rule e).
 */
export async function loadListing(id: string): Promise<Listing | null> {
  await connection();
  const result = await query({
    query: TaskListingDocument,
    variables: { id },
    errorPolicy: 'all',
  });
  if (result.data?.taskListing) return result.data.taskListing;
  if (apiErrorCode(result.error) === 'TASK_NOT_FOUND') return null;
  throw result.error ?? new Error('The task listing query returned no data.');
}

export interface OpenTasksFilter {
  areaSlug?: string;
  category?: TaskCategory;
  after?: string;
}

export interface OpenTasksPage {
  items: Listing[];
  endCursor: string | null;
  hasMore: boolean;
}

/**
 * One page of open tasks, newest published first (the API's neutral order, hard rule k), or
 * null when the area or cursor is unknown (SERVICE_AREA_NOT_FOUND, INVALID_CURSOR).
 */
export async function loadOpenTasks(filter: OpenTasksFilter): Promise<OpenTasksPage | null> {
  await connection();
  const result = await query({
    query: OpenTasksDocument,
    variables: { first: 20, ...filter },
    errorPolicy: 'all',
  });
  const page = result.data?.openTasks;
  if (page) return { items: page.items, endCursor: page.endCursor ?? null, hasMore: page.hasMore };
  const code = apiErrorCode(result.error);
  if (code === 'SERVICE_AREA_NOT_FOUND' || code === 'INVALID_CURSOR') return null;
  throw result.error ?? new Error('The open tasks query returned no data.');
}

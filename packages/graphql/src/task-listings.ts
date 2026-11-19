/**
 * Paging through task listings (the helper feed and the public browse list, BEAV-88). The
 * API returns one page at a time, newest published first; the cache keeps one growing list per
 * filter and appends each next page, dropping any task already on it (a task can move between
 * pages when one before it closes).
 */

/** Item ids, whether the cache passes plain objects or references. */
type CacheItem = { id?: string; __ref?: string };

function keyOf(item: CacheItem): string | undefined {
  return item.__ref ?? item.id;
}

/**
 * Merge an incoming page into the list so far. A page without `after` starts the list again
 * (a refetch); a page with `after` is appended.
 */
export function mergeListingPages<Page extends { items: readonly CacheItem[] }>(
  existing: Page | undefined,
  incoming: Page,
  after: string | null | undefined,
): Page {
  if (!existing || !after) return incoming;
  const seen = new Set(existing.items.map(keyOf));
  const added = incoming.items.filter((item) => !seen.has(keyOf(item)));
  return { ...incoming, items: [...existing.items, ...added] };
}

/** The variables for the next page, or null when the list is complete. */
export function nextPageVariables(
  page: { endCursor?: string | null; hasMore: boolean } | null | undefined,
): { after: string } | null {
  if (!page?.hasMore || !page.endCursor) return null;
  return { after: page.endCursor };
}

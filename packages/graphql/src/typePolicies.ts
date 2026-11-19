import type { TypePolicies } from '@apollo/client';
import { mergeListingPages } from './task-listings';

type PageValue = { items: readonly { id?: string; __ref?: string }[] };

function listingField(keyArgs: string[]) {
  return {
    keyArgs,
    merge(
      existing: PageValue | undefined,
      incoming: PageValue,
      // Structural, not Apollo's options type: web and mobile have separate Apollo copies.
      { args }: { args: Record<string, unknown> | null },
    ): PageValue {
      return mergeListingPages(existing, incoming, args?.after as string | null | undefined);
    },
  };
}

/**
 * InMemoryCache type policies shared by web and mobile. Every type exposes `id`, so the
 * default `__typename:id` key normalises them; add a policy only for `keyFields` on a type
 * without a plain `id`, `merge` for a paginated list, or `read` for a computed field.
 *
 * Task listing pages (BEAV-88) are kept inside their query field, one growing list per filter,
 * rather than normalised by their per-page `id`; their items still normalise as `TaskListing`.
 *
 * `satisfies` rather than an annotation: web and mobile resolve separate @apollo/client
 * copies (different React peers), whose class types don't match nominally.
 */
export const typePolicies = {
  TaskListingPage: { keyFields: false },
  Query: {
    fields: {
      taskFeed: listingField(['category']),
      openTasks: listingField(['areaSlug', 'category']),
    },
  },
} satisfies TypePolicies;

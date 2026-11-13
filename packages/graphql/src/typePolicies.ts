import type { TypePolicies } from '@apollo/client';

/**
 * InMemoryCache type policies shared by web and mobile. Every type exposes `id`, so the
 * default `__typename:id` key normalises them; add a policy only for `keyFields` on a type
 * without a plain `id`, `merge` for a paginated list, or `read` for a computed field.
 *
 * `satisfies` rather than an annotation: web and mobile resolve separate @apollo/client
 * copies (different React peers), whose class types don't match nominally.
 */
export const typePolicies = {} satisfies TypePolicies;

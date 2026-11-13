import { Kind, parse, visit, BREAK } from 'graphql';

/**
 * Mutations whose results hold session tokens, or that end this browser's session
 * (`signOutEverywhere`, which must also delete the cookies). They run only in the
 * /api/auth route handlers, which keep the tokens in httpOnly cookies; /api/graphql
 * refuses them so page code never sees a token. The names are Mutation fields only, so matching any field of
 * that name (aliased, in a fragment or not) can't catch anything else.
 */
const TOKEN_FIELDS = new Set([
  'verifyEmailCode',
  'signInWithProvider',
  'linkProviderWithEmailCode',
  'completeAccountDetails',
  'refreshSession',
  'signOut',
  'signOutEverywhere',
]);

/** True when `query` selects a token-returning field. Unparseable queries are left to the API. */
export function selectsTokenField(query: string): boolean {
  let document;
  try {
    document = parse(query);
  } catch {
    return false;
  }
  let found = false;
  visit(document, {
    [Kind.FIELD](node) {
      if (TOKEN_FIELDS.has(node.name.value)) {
        found = true;
        return BREAK;
      }
      return undefined;
    },
  });
  return found;
}

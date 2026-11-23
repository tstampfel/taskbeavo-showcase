import { redirectOutcome } from '@taskbeavo/graphql';

/** The query parameters Stripe adds to `return_url`; the page drops them once read. */
export const STRIPE_RETURN_PARAMS = [
  'payment_intent',
  'payment_intent_client_secret',
  'redirect_status',
] as const;

/**
 * A page loaded back from a bank's page (3-D Secure): the outcome, and the same address without
 * Stripe's parameters, so the client secret leaves the address bar and history. Null when the
 * page wasn't a return from Stripe. Used by checkout and the tip (BEAV-118 review R1-2).
 */
export function stripeReturn(url: URL): { outcome: 'submitted' | 'failed'; cleanUrl: URL } | null {
  if (!url.searchParams.has('payment_intent')) return null;
  const cleanUrl = new URL(url);
  STRIPE_RETURN_PARAMS.forEach((name) => cleanUrl.searchParams.delete(name));
  return {
    outcome: redirectOutcome(url.searchParams.get('redirect_status')) ?? 'failed',
    cleanUrl,
  };
}

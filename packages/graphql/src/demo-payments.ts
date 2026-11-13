/**
 * Demo payments (BEAV-198): the demo card form on web and mobile, used instead of Stripe's
 * card form when the API runs with DEMO_PAYMENTS=1 (a showcase without Stripe keys).
 *
 * THIS IS A DEMO STUB. It posts the PaymentIntent's client secret to the API's demo stand-in
 * for Stripe's confirm endpoint (backend/payments/demo_views.py), which marks the demo
 * PaymentIntent paid or declined and queues the webhook event. As with Stripe, whether the
 * money is held is the server's to say: FUNDS_HELD comes from the webhook (hard rule a).
 */

/** Stripe's test card numbers, which the demo stand-in also understands. */
export const DEMO_CARDS = {
  pays: '4242424242424242',
  declines: '4000000000000002',
} as const;

export type DemoCard = keyof typeof DEMO_CARDS;

/** "Demo payments on" when the client's build flag says so (NEXT_PUBLIC_ / EXPO_PUBLIC_DEMO_PAYMENTS). */
export function demoPaymentsOn(flag: string | undefined): boolean {
  return flag === '1' || flag === 'true';
}

/** The PaymentIntent ID inside a client secret (`pi_…_secret_…`), or null. */
export function intentIdOf(clientSecret: string): string | null {
  const match = /^(pi_[A-Za-z0-9]+)_secret_/.exec(clientSecret);
  return match?.[1] ?? null;
}

/** The part of `fetch` this needs (this package has no DOM types): pass the global `fetch`. */
export type DemoFetch = (
  url: string,
  init: { method: string; headers: Record<string, string>; body: string },
) => Promise<{ ok: boolean; json(): Promise<unknown> }>;

/** The demo confirm endpoint's base, next to the GraphQL endpoint: `http://host:8000/demo-stripe`. */
export function demoStripeBase(graphqlUrl: string): string {
  return graphqlUrl.replace(/\/graphql\/?$/, '') + '/demo-stripe';
}

/**
 * Confirms the PaymentIntent with the demo card. `paid` or `declined` as the stand-in says
 * (the server still confirms through the webhook); `failed` when it couldn't be reached.
 */
export async function confirmDemoPayment(
  base: string,
  clientSecret: string,
  card: DemoCard,
  fetcher: DemoFetch,
): Promise<'paid' | 'declined' | 'failed'> {
  const intent = intentIdOf(clientSecret);
  if (!intent) return 'failed';
  try {
    const response = await fetcher(`${base}/v1/payment_intents/${intent}/confirm`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ client_secret: clientSecret, card_number: DEMO_CARDS[card] }),
    });
    if (!response.ok) return 'failed';
    const body = (await response.json()) as { status?: string };
    if (body.status === 'succeeded') return 'paid';
    return body.status === 'requires_payment_method' ? 'declined' : 'failed';
  } catch {
    return 'failed';
  }
}

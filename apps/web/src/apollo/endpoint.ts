/**
 * Same-origin path the browser posts GraphQL to. Its route handler (app/api/graphql)
 * forwards to the API with the session from the httpOnly cookie.
 */
export const BROWSER_GRAPHQL_PATH = '/api/graphql';

/** The Django GraphQL endpoint, called directly by server components and SSR. */
export function serverGraphqlUrl(env: Record<string, string | undefined> = process.env): string {
  return env.GRAPHQL_URL || 'http://localhost:8000/graphql';
}

/**
 * The subscriptions WebSocket. Vercel can't hold WebSockets, so the browser connects
 * straight to the API on Render (wss://api.../graphql), set by NEXT_PUBLIC_GRAPHQL_WS_URL.
 */
export function browserGraphqlWsUrl(
  env: Record<string, string | undefined> = {
    // Spelled out so Next.js inlines it into the browser bundle.
    NEXT_PUBLIC_GRAPHQL_WS_URL: process.env.NEXT_PUBLIC_GRAPHQL_WS_URL,
  },
): string {
  return env.NEXT_PUBLIC_GRAPHQL_WS_URL || 'ws://localhost:8000/graphql';
}

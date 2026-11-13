import type { TypedDocumentNode } from '@apollo/client';
import { print } from 'graphql';
import { serverGraphqlUrl } from '../apollo/endpoint';
import { clientIp } from './client-ip';

/** One entry of a GraphQL response's `errors`; the client translates `extensions.code`. */
export interface GraphQLErrorBody {
  message: string;
  extensions?: { code?: string };
}

export interface GraphQLResponseBody<TData> {
  data?: TData | null;
  errors?: GraphQLErrorBody[];
}

let reportedMissingSecret = false;

/**
 * Logs once per server process when a production deployment has no WEB_PROXY_SECRET:
 * the API then sees every visitor at Vercel's address, so one busy minute rate-limits
 * sign-in for everyone. The dev server (and the e2e tests that run it) has none on purpose.
 */
export function reportMissingProxySecret(
  env: Record<string, string | undefined>,
  log: (message: string) => void = console.error,
): void {
  if (reportedMissingSecret || env.NODE_ENV !== 'production' || env.WEB_PROXY_SECRET) return;
  reportedMissingSecret = true;
  log(
    'WEB_PROXY_SECRET is not set: the API cannot tell web visitors apart for its rate limit. ' +
      "Set it to the same value as the backend's WEB_PROXY_SECRET.",
  );
}

/** For tests: report again. */
export function resetMissingProxySecretReport(): void {
  reportedMissingSecret = false;
}

/**
 * Headers for a route handler's call to Django on behalf of `request`'s visitor: the
 * access token as a bearer token, the trace context, and the visitor's address with the
 * shared WEB_PROXY_SECRET, so the mutation rate limit counts each visitor separately
 * (backend config/graphql_limits.py). Without the secret the address isn't sent, and
 * Django falls back to the proxy chain it trusts (GRAPHQL_TRUSTED_PROXY_COUNT).
 */
export function upstreamHeaders(
  request: { headers: Headers },
  accessToken: string | undefined,
  env: Record<string, string | undefined> = process.env,
): Headers {
  const headers = new Headers({ 'content-type': 'application/json', accept: 'application/json' });
  if (accessToken) headers.set('authorization', `Bearer ${accessToken}`);
  for (const name of ['traceparent', 'tracestate']) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  reportMissingProxySecret(env);
  const secret = env.WEB_PROXY_SECRET;
  const address = clientIp(request.headers);
  if (secret && address) {
    headers.set('x-taskbeavo-proxy-secret', secret);
    headers.set('x-taskbeavo-client-ip', address);
  }
  return headers;
}

/** Runs one operation against Django. A network failure or non-JSON answer throws. */
export async function callApi<TData, TVariables>(
  document: TypedDocumentNode<TData, TVariables>,
  variables: TVariables,
  headers: Headers,
): Promise<GraphQLResponseBody<TData>> {
  const response = await fetch(serverGraphqlUrl(), {
    method: 'POST',
    headers,
    body: JSON.stringify({ query: print(document), variables }),
    cache: 'no-store',
  });
  return (await response.json()) as GraphQLResponseBody<TData>;
}

/** The first error code of a response, if any. */
export function errorCode(body: GraphQLResponseBody<unknown>): string | undefined {
  return body.errors?.[0]?.extensions?.code;
}

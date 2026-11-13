import { ApolloClient, ApolloLink, HttpLink, InMemoryCache, gql } from '@apollo/client';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { telemetryLink } from './telemetry-links';

const sentry = vi.hoisted(() => ({ captureException: vi.fn(), addBreadcrumb: vi.fn() }));
vi.mock('@sentry/nextjs', () => sentry);

const TRACEPARENT = /^00-([0-9a-f]{32})-[0-9a-f]{16}-01$/;
const QUERY = gql`
  query ApiVersion {
    apiVersion
  }
`;
const MUTATION = gql`
  mutation SetLocale($locale: String!) {
    setLocale(locale: $locale)
  }
`;

function client(fetch: typeof globalThis.fetch) {
  return new ApolloClient({
    cache: new InMemoryCache(),
    link: ApolloLink.from([telemetryLink(), new HttpLink({ uri: '/api/graphql', fetch })]),
  });
}

function respond(status: number, body: unknown) {
  return vi.fn<typeof globalThis.fetch>(
    async () =>
      new Response(JSON.stringify(body), {
        status,
        headers: { 'content-type': 'application/json' },
      }),
  );
}

beforeEach(() => vi.clearAllMocks());

describe('telemetryLink', () => {
  it('sends a new W3C traceparent with every request', async () => {
    const fetch = respond(200, { data: { apiVersion: '1' } });
    const apollo = client(fetch);
    await apollo.query({ query: QUERY, fetchPolicy: 'network-only' });
    await apollo.query({ query: QUERY, fetchPolicy: 'network-only' });

    const sent = fetch.mock.calls.map(
      ([, init]) => new Headers(init?.headers).get('traceparent') ?? '',
    );
    expect(sent[0]).toMatch(TRACEPARENT);
    expect(sent[1]).toMatch(TRACEPARENT);
    expect(sent[0]).not.toBe(sent[1]);
  });

  it('reports a failed request to Sentry with the operation name and its trace', async () => {
    const fetch = respond(502, { message: 'Bad gateway' });
    await expect(
      client(fetch).query({ query: QUERY, fetchPolicy: 'network-only' }),
    ).rejects.toThrow();

    const traceId = TRACEPARENT.exec(
      new Headers(fetch.mock.calls[0]?.[1]?.headers).get('traceparent') ?? '',
    )?.[1];
    expect(sentry.captureException).toHaveBeenCalledWith(expect.anything(), {
      tags: {
        'graphql.operation.name': 'ApiVersion',
        'graphql.operation.type': 'query',
        trace_id: traceId,
      },
    });
  });

  it("doesn't report a signed-out SetLocale (UNAUTHENTICATED) or send its variables", async () => {
    const fetch = respond(200, {
      data: null,
      errors: [{ message: 'Sign in first.', extensions: { code: 'UNAUTHENTICATED' } }],
    });
    await expect(
      client(fetch).mutate({ mutation: MUTATION, variables: { locale: 'de' } }),
    ).rejects.toThrow();

    expect(sentry.captureException).not.toHaveBeenCalled();
    expect(sentry.addBreadcrumb).toHaveBeenCalledWith(
      expect.objectContaining({
        message: 'SetLocale',
        data: expect.objectContaining({ codes: ['UNAUTHENTICATED'] }),
      }),
    );
    expect(JSON.stringify(sentry.addBreadcrumb.mock.calls)).not.toContain('"de"');
  });
});

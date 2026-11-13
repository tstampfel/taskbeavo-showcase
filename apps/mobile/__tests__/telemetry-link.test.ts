import { ApolloClient, ApolloLink, HttpLink, InMemoryCache, gql } from '@apollo/client';
import * as Sentry from '@sentry/react-native';
import { telemetryLink } from '../src/apollo/telemetry-link';

jest.mock('@sentry/react-native');

const TRACEPARENT = /^00-([0-9a-f]{32})-[0-9a-f]{16}-01$/;
const QUERY = gql`
  query ApiVersion {
    apiVersion
  }
`;

function respond(status: number, body: unknown) {
  return jest.fn<Promise<Response>, Parameters<typeof fetch>>(
    async () =>
      ({
        ok: status < 300,
        status,
        headers: { get: () => 'application/json' },
        text: async () => JSON.stringify(body),
      }) as unknown as Response,
  );
}

function client(fetchFn: typeof fetch) {
  return new ApolloClient({
    cache: new InMemoryCache(),
    link: ApolloLink.from([
      telemetryLink(),
      new HttpLink({ uri: 'http://api/graphql', fetch: fetchFn }),
    ]),
  });
}

function sentTraceparent(fetchFn: jest.Mock, call = 0): string {
  const init = fetchFn.mock.calls[call]?.[1] as RequestInit | undefined;
  return (init?.headers as Record<string, string>).traceparent ?? '';
}

beforeEach(() => jest.clearAllMocks());

describe('telemetryLink', () => {
  it('sends a new W3C traceparent with every request', async () => {
    const fetchFn = respond(200, { data: { apiVersion: '1' } });
    const apollo = client(fetchFn);
    await apollo.query({ query: QUERY, fetchPolicy: 'network-only' });
    await apollo.query({ query: QUERY, fetchPolicy: 'network-only' });
    expect(sentTraceparent(fetchFn, 0)).toMatch(TRACEPARENT);
    expect(sentTraceparent(fetchFn, 1)).toMatch(TRACEPARENT);
    expect(sentTraceparent(fetchFn, 0)).not.toBe(sentTraceparent(fetchFn, 1));
  });

  it('reports a server error to Sentry with the operation name and its trace', async () => {
    const fetchFn = respond(500, { message: 'oops' });
    await expect(
      client(fetchFn).query({ query: QUERY, fetchPolicy: 'network-only' }),
    ).rejects.toThrow();
    expect(Sentry.captureException).toHaveBeenCalledWith(expect.anything(), {
      tags: {
        'graphql.operation.name': 'ApiVersion',
        'graphql.operation.type': 'query',
        trace_id: TRACEPARENT.exec(sentTraceparent(fetchFn))?.[1],
      },
    });
  });

  it('keeps expected GraphQL errors out of Sentry', async () => {
    const fetchFn = respond(200, {
      data: null,
      errors: [{ message: 'Sign in first.', extensions: { code: 'UNAUTHENTICATED' } }],
    });
    await expect(
      client(fetchFn).query({ query: QUERY, fetchPolicy: 'network-only' }),
    ).rejects.toThrow();
    expect(Sentry.captureException).not.toHaveBeenCalled();
    expect(Sentry.addBreadcrumb).toHaveBeenCalledWith(
      expect.objectContaining({
        message: 'ApiVersion',
        data: expect.objectContaining({ codes: ['UNAUTHENTICATED'] }),
      }),
    );
  });
});

import { describe, expect, it, vi } from 'vitest';
import {
  FILTERED,
  euSentryDsn,
  isExpectedApolloError,
  newTraceContext,
  reportApolloError,
  scrubBreadcrumb,
  scrubEvent,
  scrubText,
  sentryBeforeSend,
  traceContextOf,
  withTraceContext,
  type ErrorReporter,
} from './telemetry';

const TRACEPARENT = /^00-([0-9a-f]{32})-([0-9a-f]{16})-01$/;

function operation(name: string | undefined, context: Record<string, unknown> = {}) {
  return {
    operationName: name,
    operationType: 'mutation',
    getContext: () => context,
  };
}

function reporter() {
  return {
    captureException: vi.fn<ErrorReporter['captureException']>(),
    addBreadcrumb: vi.fn<ErrorReporter['addBreadcrumb']>(),
  };
}

function graphQLErrors(...codes: (string | undefined)[]) {
  const error = new Error('GraphQL errors') as Error & { errors: unknown[] };
  error.name = 'CombinedGraphQLErrors';
  error.errors = codes.map((code) => ({
    message: 'Ana Horvat, ana@example.com',
    extensions: code ? { code } : {},
  }));
  return error;
}

describe('traceparent', () => {
  it('builds a W3C traceparent with a fresh, sampled trace', () => {
    const first = newTraceContext();
    const second = newTraceContext();
    expect(first.traceparent).toMatch(TRACEPARENT);
    expect(first.traceparent).toBe(`00-${first.traceId}-${first.spanId}-01`);
    expect(second.traceId).not.toBe(first.traceId);
  });

  it('never sends the all-zero IDs the spec forbids', () => {
    const zeros = newTraceContext((bytes) => bytes.fill(0));
    expect(zeros.traceId).not.toMatch(/^0+$/);
    expect(zeros.spanId).not.toMatch(/^0+$/);
    expect(zeros.traceparent).toMatch(TRACEPARENT);
  });

  it('works without Web Crypto (React Native)', () => {
    vi.stubGlobal('crypto', undefined);
    try {
      expect(newTraceContext().traceparent).toMatch(TRACEPARENT);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('adds the traceparent header to every operation and keeps the existing headers', () => {
    const context = withTraceContext({ headers: { 'x-taskbeavo-csrf': '1' } });
    expect(context.headers['x-taskbeavo-csrf']).toBe('1');
    expect(context.headers.traceparent).toMatch(TRACEPARENT);
    expect(traceContextOf(operation('A', context))?.traceparent).toBe(context.headers.traceparent);
  });

  it('starts a new trace for each operation', () => {
    expect(withTraceContext({}).headers.traceparent).not.toBe(
      withTraceContext({}).headers.traceparent,
    );
  });
});

describe('Apollo errors', () => {
  it('treats GraphQL errors that carry an error code as answers, not failures', () => {
    // BEAV-65: the language switcher's SetLocale while signed out.
    expect(isExpectedApolloError(graphQLErrors('UNAUTHENTICATED'))).toBe(true);
    expect(isExpectedApolloError(graphQLErrors('RATE_LIMITED', 'UNAUTHENTICATED'))).toBe(true);
    expect(isExpectedApolloError(graphQLErrors('UNAUTHENTICATED', undefined))).toBe(false);
    expect(isExpectedApolloError(new Error('boom'))).toBe(false);
    expect(isExpectedApolloError(undefined)).toBe(false);
  });

  it('records GraphQL errors as a breadcrumb with the operation name and codes only', () => {
    const sentry = reporter();
    const context = withTraceContext({});
    reportApolloError(sentry, graphQLErrors('UNAUTHENTICATED'), operation('SetLocale', context));
    expect(sentry.captureException).not.toHaveBeenCalled();
    expect(sentry.addBreadcrumb).toHaveBeenCalledWith({
      category: 'graphql',
      level: 'info',
      message: 'SetLocale',
      data: {
        codes: ['UNAUTHENTICATED'],
        trace_id: traceContextOf(operation('SetLocale', context))?.traceId,
      },
    });
    expect(JSON.stringify(sentry.addBreadcrumb.mock.calls)).not.toContain('ana');
  });

  it('leaves GraphQL errors without a code to the backend, which reports them in the same trace', () => {
    const sentry = reporter();
    reportApolloError(sentry, graphQLErrors(undefined), operation('ApiVersion'));
    expect(sentry.captureException).not.toHaveBeenCalled();
    expect(sentry.addBreadcrumb.mock.calls[0]?.[0].data?.codes).toEqual(['UNKNOWN']);
  });

  it('reports a server or parse error tagged with the operation and its trace', () => {
    const sentry = reporter();
    const context = withTraceContext({});
    const error = Object.assign(new Error('Response not successful'), {
      name: 'ServerError',
      statusCode: 502,
    });
    reportApolloError(sentry, error, operation('AcceptOffer', context));
    expect(sentry.captureException).toHaveBeenCalledWith(error, {
      tags: {
        'graphql.operation.name': 'AcceptOffer',
        'graphql.operation.type': 'mutation',
        trace_id: traceContextOf(operation('AcceptOffer', context))?.traceId,
      },
    });
  });

  it("doesn't report the device being offline", () => {
    const sentry = reporter();
    for (const message of ['Failed to fetch', 'Network request failed', 'Load failed']) {
      reportApolloError(sentry, new TypeError(message), operation('ApiVersion'));
    }
    expect(sentry.captureException).not.toHaveBeenCalled();
    expect(sentry.addBreadcrumb).toHaveBeenCalledTimes(3);
  });
});

describe('scrubbing', () => {
  it('replaces emails, IBANs, OIBs and phone numbers in text', () => {
    const text = scrubText(
      'ana@example.com HR0600000000000000160 00000000124 +385 91 234 5678 091 234 5678 booking 42',
    );
    expect(text).not.toMatch(/ana@|HR06|00000000124|385|091/);
    expect(text).toContain('booking 42');
  });

  it('keeps the stack and the error type but scrubs messages', () => {
    const event = scrubEvent({
      exception: {
        values: [
          {
            type: 'Error',
            value: 'No helper for ana@example.com',
            stacktrace: { frames: [{ filename: 'app:///index.js', lineno: 1, vars: { a: 1 } }] },
          },
        ],
      },
      message: 'call +385 91 234 5678',
    });
    const value = event.exception?.values?.[0];
    expect(value?.type).toBe('Error');
    expect(value?.value).toBe(`No helper for ${FILTERED}`);
    expect(value?.stacktrace?.frames?.[0]).toEqual({ filename: 'app:///index.js', lineno: 1 });
    expect(event.message).toBe(`call ${FILTERED}`);
  });

  it('strips request bodies, cookies, query strings and personal headers', () => {
    const event = scrubEvent({
      request: {
        method: 'POST',
        url: 'https://taskbeavo.hr/api/graphql?email=ana@example.com#x',
        data: '{"variables":{"iban":"HR0600000000000000160"}}',
        cookies: { tb_session: 'secret' },
        query_string: 'email=ana@example.com',
        headers: {
          'Content-Type': 'application/json',
          Cookie: 'tb_session=secret',
          Authorization: 'Bearer x',
          'User-Agent': 'Mozilla/5.0',
          traceparent: '00-a-b-01',
        },
      },
    });
    expect(event.request).toEqual({
      method: 'POST',
      url: 'https://taskbeavo.hr/api/graphql',
      headers: {
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0',
        traceparent: '00-a-b-01',
      },
    });
  });

  it('keeps only the user id', () => {
    const event = scrubEvent({
      user: { id: 7, email: 'ana@example.com', username: 'ana', ip_address: '1.2.3.4' },
    });
    expect(event.user).toEqual({ id: '7' });
  });

  it('drops unknown event keys and contexts, and the device name', () => {
    const event = scrubEvent({
      level: 'error',
      debug_meta: { images: [{ type: 'sourcemap', debug_id: 'x' }] },
      state: { draft: 'Mama lives at Ulica 1' },
      contexts: {
        os: { name: 'iOS' },
        device: { name: "Ana's iPhone", model: 'iPhone16,1' },
        state: { form: 'x' },
      },
    } as Record<string, unknown>);
    expect(event).not.toHaveProperty('state');
    expect(event.debug_meta).toEqual({ images: [{ type: 'sourcemap', debug_id: 'x' }] });
    expect(event.contexts).toEqual({ os: { name: 'iOS' }, device: { model: 'iPhone16,1' } });
  });

  it('filters values under personal keys in extra and tags, but keeps allow-listed tags', () => {
    const event = scrubEvent({
      extra: { phoneNumber: '1', chatText: 'hi', nested: { gate_code: '1234' }, bookingCount: 2 },
      tags: { 'graphql.operation.name': 'SetLocale', 'user.id': '7', full_name: 'Ana' },
    });
    expect(event.extra).toEqual({
      phoneNumber: FILTERED,
      chatText: FILTERED,
      nested: { gate_code: FILTERED },
      bookingCount: 2,
    });
    expect(event.tags).toEqual({
      'graphql.operation.name': 'SetLocale',
      'user.id': '7',
      full_name: FILTERED,
    });
  });

  it('scrubs breadcrumbs: no console text, no query strings, no labels in selectors', () => {
    expect(
      scrubBreadcrumb({
        category: 'console',
        level: 'log',
        message: 'chat: see you at Ulica 1',
        data: { arguments: ['chat', 'see you'] },
      }),
    ).toEqual({ category: 'console', level: 'log', message: FILTERED });
    expect(
      scrubBreadcrumb({
        category: 'fetch',
        data: { method: 'POST', url: '/api/graphql?token=x', status_code: 200 },
      }).data,
    ).toEqual({ method: 'POST', url: '/api/graphql', status_code: 200 });
    expect(
      scrubBreadcrumb({ category: 'navigation', data: { from: '/hr?q=ana', to: '/de/tasks/4' } })
        .data,
    ).toEqual({ from: '/hr', to: '/de/tasks/4' });
    expect(
      scrubBreadcrumb({ category: 'ui.click', message: 'button[aria-label="Message Ana Horvat"]' })
        .message,
    ).toBe('button[aria-label]');
  });

  it('scrubs breadcrumbs inside events', () => {
    const event = scrubEvent({
      breadcrumbs: [{ category: 'console', message: 'ana@example.com' }],
    });
    expect(event.breadcrumbs).toEqual([{ category: 'console', message: FILTERED }]);
  });
});

describe('sentryBeforeSend', () => {
  it('drops expected GraphQL errors that a component rethrows', () => {
    const error = graphQLErrors('UNAUTHENTICATED');
    expect(sentryBeforeSend({ exception: { values: [] } }, { originalException: error })).toBe(
      null,
    );
  });

  it('sends the codes of unexpected GraphQL errors, never the server messages', () => {
    // graphql-core echoes invalid input, and the backend doesn't mask unexpected errors.
    const leaked =
      'Variable \'$input\' got invalid value {street: "Ulica Ante Starčevića 5", name: "Ana Horvat"}';
    const error = graphQLErrors('RATE_LIMITED', undefined);
    error.message = leaked;
    const event = sentryBeforeSend(
      {
        exception: { values: [{ type: 'CombinedGraphQLErrors', value: leaked }] },
        message: leaked,
      },
      { originalException: error },
    );
    expect(event?.exception?.values?.[0]?.value).toBe('GraphQL errors: RATE_LIMITED, UNKNOWN');
    expect(JSON.stringify(event)).not.toMatch(/Starčevića|Ana Horvat/);
  });

  it('withholds the messages of GraphQL and protocol errors even without the original error', () => {
    const event = scrubEvent({
      exception: {
        values: [
          { type: 'CombinedGraphQLErrors', value: 'Key (name)=(Ana Horvat) already exists' },
          { type: 'CombinedProtocolErrors', value: 'gate code 4321 rejected' },
          { type: 'TypeError', value: 'x is undefined' },
        ],
      },
    });
    expect(event.exception?.values?.map((value) => value.value)).toEqual([
      'GraphQL errors',
      'GraphQL errors',
      'x is undefined',
    ]);
  });

  it('scrubs everything else', () => {
    const event = sentryBeforeSend({ message: 'ana@example.com' }, { originalException: 'x' });
    expect(event?.message).toBe(FILTERED);
  });
});

describe('euSentryDsn', () => {
  it("accepts only DSNs in Sentry's EU region", () => {
    const eu = 'https://abc@o1.ingest.de.sentry.io/2';
    expect(euSentryDsn(eu)).toBe(eu);
    expect(euSentryDsn('https://abc@o1.ingest.us.sentry.io/2')).toBeUndefined();
    expect(euSentryDsn('https://abc@evil.example/de.sentry.io/2')).toBeUndefined();
    expect(euSentryDsn('')).toBeUndefined();
    expect(euSentryDsn(undefined)).toBeUndefined();
  });
});

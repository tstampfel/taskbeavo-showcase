import { describe, expect, it } from 'vitest';
import { sentryOptions } from './options';

const EU_DSN = 'https://key@o1.ingest.de.sentry.io/2';

describe('sentryOptions', () => {
  it('sends errors only, with no personal data and no Session Replay', () => {
    const options = sentryOptions({ dsn: EU_DSN, environment: 'production' });
    expect(options).toMatchObject({
      dsn: EU_DSN,
      enabled: true,
      environment: 'production',
      sendDefaultPii: false,
      tracePropagationTargets: [],
      replaysSessionSampleRate: 0,
      replaysOnErrorSampleRate: 0,
    });
    expect(options).not.toHaveProperty('tracesSampleRate');
    expect(options).not.toHaveProperty('integrations');
  });

  it('scrubs events and drops expected GraphQL errors', () => {
    const { beforeSend, beforeBreadcrumb } = sentryOptions({ dsn: EU_DSN });
    expect(beforeSend({ message: 'ana@example.com' }, {})?.message).toBe('[Filtered]');
    const unauthenticated = Object.assign(new Error('x'), {
      name: 'CombinedGraphQLErrors',
      errors: [{ message: 'x', extensions: { code: 'UNAUTHENTICATED' } }],
    });
    expect(beforeSend({}, { originalException: unauthenticated })).toBeNull();
    expect(beforeBreadcrumb({ category: 'console', message: 'x' }).message).toBe('[Filtered]');
  });

  it("stays off without a DSN or with one outside Sentry's EU region", () => {
    expect(sentryOptions({}).enabled).toBe(false);
    const us = sentryOptions({ dsn: 'https://key@o1.ingest.us.sentry.io/2' });
    expect(us.enabled).toBe(false);
    expect(us.dsn).toBeUndefined();
  });
});

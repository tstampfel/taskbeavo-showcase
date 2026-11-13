import { sentryOptions } from '../src/sentry/options';

const EU_DSN = 'https://key@o1.ingest.de.sentry.io/2';

describe('sentryOptions', () => {
  it('sends errors only, with no personal data, replay, screenshots or view hierarchy', () => {
    const options = sentryOptions({ dsn: EU_DSN, environment: 'production' });
    expect(options).toMatchObject({
      dsn: EU_DSN,
      enabled: true,
      environment: 'production',
      sendDefaultPii: false,
      tracePropagationTargets: [],
      enableAutoPerformanceTracing: false,
      replaysSessionSampleRate: 0,
      replaysOnErrorSampleRate: 0,
      attachScreenshot: false,
      attachViewHierarchy: false,
    });
    expect(options).not.toHaveProperty('tracesSampleRate');
    expect(options).not.toHaveProperty('integrations');
  });

  it('scrubs events and drops expected GraphQL errors', () => {
    const { beforeSend } = sentryOptions({ dsn: EU_DSN });
    expect(beforeSend({ message: 'call +385 91 234 5678' }, {})?.message).toBe('call [Filtered]');
    const unauthenticated = Object.assign(new Error('x'), {
      name: 'CombinedGraphQLErrors',
      errors: [{ message: 'x', extensions: { code: 'UNAUTHENTICATED' } }],
    });
    expect(beforeSend({}, { originalException: unauthenticated })).toBeNull();
  });

  it('stores and sends no persistent device identifier (BEAV-190)', () => {
    const options = sentryOptions({ dsn: EU_DSN });
    // Release health sessions carry the SDK's installation ID.
    expect(options.enableAutoSessionTracking).toBe(false);
    // The iOS SDK adds its installation ID as the user of every event.
    const event = options.beforeSend({ message: 'x', user: { id: 'C0FFEE-INSTALLATION' } }, {});
    expect(event).toEqual({ message: 'x' });
  });

  it('drops the device and vendor identifiers the native SDKs add (BEAV-190, QA F5)', () => {
    const { beforeSend } = sentryOptions({ dsn: EU_DSN });
    const event = beforeSend(
      {
        message: 'x',
        contexts: {
          // iOS: a SHA-1 of identifierForVendor, the same for every event from this install.
          app: {
            app_identifier: 'com.taskbeavo.app',
            device_app_hash: '7f5ecadb',
            app_version: '1',
          },
          // Android: the installation ID.
          device: { id: 'C0FFEE-INSTALLATION', family: 'iOS', model: 'iPhone18,3' },
          os: { name: 'iOS', version: '26.5' },
        },
      },
      {},
    );
    expect(event?.contexts).toEqual({
      app: { app_identifier: 'com.taskbeavo.app', app_version: '1' },
      device: { family: 'iOS', model: 'iPhone18,3' },
      os: { name: 'iOS', version: '26.5' },
    });
  });

  it("stays off without a DSN or with one outside Sentry's EU region", () => {
    expect(sentryOptions({}).enabled).toBe(false);
    expect(sentryOptions({ dsn: 'https://key@o1.ingest.sentry.io/2' }).enabled).toBe(false);
  });
});

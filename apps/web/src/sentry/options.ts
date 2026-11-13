import { euSentryDsn, sentryBeforeBreadcrumb, sentryBeforeSend } from '@taskbeavo/graphql';

/**
 * Sentry options for the browser, the Node.js server and the edge runtime. Errors only:
 * tracing is Honeycomb's, through the traceparent the Apollo link sends. No Session Replay
 * (screens show addresses and chats), no personal data, and every event is scrubbed by
 * @taskbeavo/graphql's telemetry helpers before it leaves.
 *
 * Sentry stays off without a DSN, and with a DSN outside Sentry's EU region.
 */
export function sentryOptions(env: { dsn?: string; environment?: string; release?: string }) {
  const dsn = euSentryDsn(env.dsn);
  return {
    dsn,
    enabled: dsn !== undefined,
    environment: env.environment ?? 'development',
    release: env.release || undefined,
    sendDefaultPii: false,
    // No sentry-trace or baggage headers: the Apollo link's traceparent carries the trace.
    tracePropagationTargets: [],
    replaysSessionSampleRate: 0,
    replaysOnErrorSampleRate: 0,
    beforeSend: sentryBeforeSend,
    beforeBreadcrumb: sentryBeforeBreadcrumb,
  };
}

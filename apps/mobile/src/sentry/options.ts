import { euSentryDsn, sentryBeforeBreadcrumb, sentryBeforeSend } from '@taskbeavo/graphql';

/**
 * Sentry options for the app. Errors and crashes only: tracing is Honeycomb's, through the
 * traceparent the Apollo link sends. No Session Replay, screenshots or view hierarchy
 * (screens show addresses and chats), no personal data, and every JavaScript event is
 * scrubbed by @taskbeavo/graphql's telemetry helpers before it leaves.
 *
 * No persistent device identifier either (BEAV-190, docs/storage-inventory.md): no release
 * health sessions (they carry the SDK's installation ID), no `user` on any event, and none of
 * the device or vendor identifiers the native SDKs add to the contexts (DEVICE_IDENTIFIERS, such
 * as iOS's `app.device_app_hash`, a hash of identifierForVendor). The iOS SDK would otherwise
 * add its installation ID as `user.id`; initSentry also gives the native scope the fixed
 * ANONYMOUS_USER, so native crashes don't fall back to it either.
 *
 * Sentry stays off without a DSN, and with a DSN outside Sentry's EU region.
 */
/** The same for every install, so it identifies no one; only stops the installation ID fallback. */
export const ANONYMOUS_USER = { id: 'anonymous' } as const;

/** Identifiers of the device, install or vendor that the native SDKs put in the contexts. */
export const DEVICE_IDENTIFIERS = {
  app: ['device_app_hash'],
  device: ['id', 'device_unique_identifier', 'installation_id'],
} as const;

type Contexts = Record<string, Record<string, unknown> | undefined>;

function withoutDeviceIdentifiers<T extends object>(
  event: T,
  hint: { originalException?: unknown } | undefined,
): T | null {
  const scrubbed = sentryBeforeSend(event, hint);
  if (!scrubbed) return scrubbed;
  if ('user' in scrubbed) delete (scrubbed as { user?: unknown }).user;
  const contexts = (scrubbed as { contexts?: Contexts }).contexts;
  for (const [context, keys] of Object.entries(DEVICE_IDENTIFIERS)) {
    const values = contexts?.[context];
    if (values) for (const key of keys) delete values[key];
  }
  return scrubbed;
}

export function sentryOptions(env: { dsn?: string; environment?: string }) {
  const dsn = euSentryDsn(env.dsn);
  return {
    dsn,
    enabled: dsn !== undefined,
    environment: env.environment ?? 'development',
    sendDefaultPii: false,
    tracePropagationTargets: [],
    enableAutoPerformanceTracing: false,
    enableUserInteractionTracing: false,
    replaysSessionSampleRate: 0,
    replaysOnErrorSampleRate: 0,
    attachScreenshot: false,
    attachViewHierarchy: false,
    enableAutoSessionTracking: false,
    beforeSend: withoutDeviceIdentifiers,
    beforeBreadcrumb: sentryBeforeBreadcrumb,
  };
}

import * as Sentry from '@sentry/nextjs';
import { sentryOptions } from './sentry/options';

/** Server and edge errors: route handlers, server components and the proxy. */
export function register() {
  if (process.env.NEXT_RUNTIME === 'nodejs' || process.env.NEXT_RUNTIME === 'edge') {
    Sentry.init(
      sentryOptions({
        dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,
        environment: process.env.VERCEL_ENV,
      }),
    );
  }
}

export const onRequestError = Sentry.captureRequestError;

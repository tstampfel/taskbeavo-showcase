import * as Sentry from '@sentry/nextjs';
import { sentryOptions } from './sentry/options';

// Browser errors. NEXT_PUBLIC_VERCEL_ENV is Vercel's production/preview/development.
Sentry.init(
  sentryOptions({
    dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,
    environment: process.env.NEXT_PUBLIC_VERCEL_ENV,
  }),
);

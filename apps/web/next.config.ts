import { withSentryConfig } from '@sentry/nextjs/config';
import type { NextConfig } from 'next';
import createNextIntlPlugin from 'next-intl/plugin';

const withNextIntl = createNextIntlPlugin('./src/i18n/request.ts');

const nextConfig: NextConfig = {
  // Workspace packages ship TypeScript source, not built output.
  transpilePackages: [
    '@taskbeavo/app-links',
    '@taskbeavo/graphql',
    '@taskbeavo/i18n',
    '@taskbeavo/ui-tokens',
  ],
  // A translated 404 for paths outside [locale], since the root layout is per-locale.
  experimental: { globalNotFound: true },
};

/**
 * Source maps upload to Sentry's EU region during `next build` on Vercel when
 * SENTRY_AUTH_TOKEN, SENTRY_ORG and SENTRY_PROJECT are set, and are then deleted so they're
 * never served. Without a token (local builds, CI) nothing is uploaded.
 */
export const sentryBuildOptions = {
  org: process.env.SENTRY_ORG,
  project: process.env.SENTRY_PROJECT,
  authToken: process.env.SENTRY_AUTH_TOKEN,
  // Same as SENTRY_EU_URL in @taskbeavo/graphql (next.config can't load workspace TypeScript).
  sentryUrl: 'https://de.sentry.io/',
  silent: !process.env.CI,
  telemetry: false,
  widenClientFileUpload: true,
  sourcemaps: {
    disable: process.env.SENTRY_AUTH_TOKEN ? false : ('disable-upload' as const),
    deleteSourcemapsAfterUpload: true,
  },
  // Tracing is Honeycomb's, so the router-transition hook isn't used.
  suppressOnRouterTransitionStartWarning: true,
};

export default withSentryConfig(withNextIntl(nextConfig), sentryBuildOptions);

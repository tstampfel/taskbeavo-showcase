# @taskbeavo/web

Next.js App Router site. Every page lives under `/hr`, `/en`, `/de` or `/fr`; `src/proxy.ts` redirects `/` by cookie or `Accept-Language`, defaulting to `hr`.

| Purpose                | Command (from the repo root)                                                                                                     |
| ---------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| Dev server             | `pnpm --filter @taskbeavo/web dev`                                                                                               |
| Unit tests             | `pnpm --filter @taskbeavo/web test`                                                                                              |
| E2E (375px and 1280px) | `pnpm --filter @taskbeavo/web exec playwright test` (first run: `pnpm --filter @taskbeavo/web exec playwright install chromium`) |

## Environment

| Variable                           | Use                                                                                                                                                                                                                                                                                                                                     |
| ---------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `NEXT_PUBLIC_SITE_URL`             | Absolute base for canonical and hreflang links, e.g. `https://taskbeavo.com`. Falls back to Vercel's `VERCEL_PROJECT_PRODUCTION_URL`, then localhost.                                                                                                                                                                                   |
| `NEXT_PUBLIC_GRAPHQL_WS_URL`       | Subscriptions WebSocket the browser connects to directly (Vercel can't hold WebSockets), e.g. `wss://api.taskbeavo.com/graphql`. Defaults to `ws://localhost:8000/graphql`.                                                                                                                                                             |
| `GRAPHQL_URL`                      | Django's GraphQL endpoint for server rendering and the `/api` route handlers, e.g. `https://api.taskbeavo.com/graphql`. Defaults to `http://localhost:8000/graphql`.                                                                                                                                                                    |
| `WEB_PROXY_SECRET`                 | Server-only. **Exactly the same value** as the backend's `WEB_PROXY_SECRET` on Render; lets the route handlers pass each visitor's IP to the API's mutation rate limit. A production server without it logs an error; a mismatch makes the API log a warning, and either way every visitor shares one rate-limit bucket. Unset locally. |
| `NEXT_PUBLIC_SENTRY_DSN`           | Sentry DSN for browser, server and edge errors. Must be in Sentry's EU region (`*.ingest.de.sentry.io`); any other DSN, or none, leaves Sentry off.                                                                                                                                                                                     |
| `SENTRY_AUTH_TOKEN`                | Build-only secret (Vercel, Production and Preview): an org token with source map upload rights. Without it `next build` uploads nothing.                                                                                                                                                                                                |
| `SENTRY_ORG`, `SENTRY_PROJECT`     | Build-only: the Sentry organisation and the web project's slugs for the upload.                                                                                                                                                                                                                                                         |
| `APPLE_TEAM_ID`                    | Server-only. The Apple Developer Team ID (10 characters) that signs the iOS app; `/.well-known/apple-app-site-association` lists `<team>.com.taskbeavo.app` and the preview and dev builds. Unset, the file is a 404.                                                                                                                   |
| `ANDROID_SHA256_CERT_FINGERPRINTS` | Server-only. Comma-separated SHA-256 signing certificate fingerprints of the production Android app (the Play app signing key **and** the upload key, from EAS credentials / Play Console) for `/.well-known/assetlinks.json`. `_PREVIEW` and `_DEVELOPMENT` suffixes add the preview and dev builds. None set, the file is a 404.      |

## App links

Links in emails and pushes are `https://taskbeavo.com/<locale>/tasks/<id>`, `/bookings/<id>`, `/bookings/<id>/release`, `/bookings/<id>/no-access`, `/conversations/<id>` and `/helper/payouts` (also Stripe onboarding's `/helper/payouts/return` and `/refresh`) (also without the locale; the proxy then adds the visitor's). With the app installed they open the matching screen (iOS universal links, Android App Links); otherwise the page here opens. The pages are placeholders for now, unindexed, and never take an action: releasing payment always needs a tap. `src/app-links/association.ts` builds `apple-app-site-association` and `assetlinks.json` from the variables above from the paths and app IDs in `@taskbeavo/app-links` (`packages/app-links`), the same module the mobile app's link handling and `app.config.ts` read. Every page under `/tasks`, `/bookings`, `/conversations` and `/helper` opens the app; a new web-only page there needs an app route or an `exclude` entry (see `LINKED_SECTIONS`).

After deploying to the production domain, check both files with Apple's AASA validator (or Branch's) and Google's Digital Asset Links API (`https://digitalassetlinks.googleapis.com/v1/statements:list?source.web.site=https://taskbeavo.com&relation=delegate_permission/common.handle_all_urls`), **on both `taskbeavo.com` and `www.taskbeavo.com`**. Each host must answer `/.well-known/*` itself with a 200: Apple and Android don't follow redirects, and Vercel's usual www→apex redirect would break www, and on Android 11 and older the apex too, since one failing host turns off verification for the whole filter. If www has to redirect, drop it from `LINK_HOSTS` in `packages/app-links` (emails use the apex, `WEB_APP_URL`). The app builds all claim the production hosts only; a staging host for preview builds is a follow-up.

## Errors and tracing

Sentry (EU) gets errors only, never Session Replay or personal data: `src/sentry/options.ts` sets `sendDefaultPii: false` and runs every event through `@taskbeavo/graphql`'s scrubber, and expected GraphQL errors (an API error code such as `UNAUTHENTICATED`) never become events. The Apollo `telemetryLink` starts a trace per operation and sends its W3C `traceparent`; `/api/graphql` forwards it to Django, whose spans continue the same trace in Honeycomb. A failed request reaches Sentry tagged with `graphql.operation.name` and `trace_id`, so the error links to its trace. `next build` uploads hidden source maps to Sentry's EU region and deletes them from the output.

## Sessions

Sign-in tokens never reach page code. `/api/auth/verify-email-code`, `/api/auth/complete-account-details` and `/api/auth/sign-out` call the API and keep the access and refresh tokens in httpOnly `SameSite=Lax` cookies (`Secure` outside local dev). `/api/graphql` forwards every other operation with the access cookie as a bearer token, refreshing it when only the refresh cookie is left, and refuses the token-returning mutations. Subscriptions get a one-minute token from the `webSocketToken` mutation for `connection_init`. Every `/api` POST must be JSON, carry `x-taskbeavo-csrf: 1` and come from this origin (`src/auth/csrf.ts`).

## Vercel setup (one-time, done by a person)

`vercel.json` pins functions to `fra1`. The project itself is created in the Vercel dashboard:

1. Create it on a **Pro** team: the Hobby plan forbids commercial use (`docs/PLAN.md`, "Stack choices and why").
2. Import the GitHub repo and set **Root Directory** to `apps/web`. Leave "Include files outside the root directory" on, so `packages/i18n` and `packages/ui-tokens` resolve.
3. Add the environment variable `ENABLE_EXPERIMENTAL_COREPACK=1` so Vercel uses the `packageManager` pin (`pnpm@12.15.0`); older pnpm versions don't understand `allowBuilds` in `pnpm-workspace.yaml`. Check the first build log for the pnpm version.
4. Set `NEXT_PUBLIC_SITE_URL` for Production once the domain exists.
5. Leave the Git integration on: every PR then gets a preview deployment. Open a PR to confirm the preview builds.

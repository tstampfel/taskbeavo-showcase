import { defineConfig, devices } from '@playwright/test';

const port = 3100;
const devPort = 3101;
const apiStubPort = 3199;

/** The dev server smoke spec runs only against `pnpm dev`; every other spec skips it. */
const devSmoke = /dev-server\.spec\.ts$/;

const appEnv = {
  GRAPHQL_URL: `http://localhost:${apiStubPort}/graphql`,
  // Never the default ws://localhost:8000/graphql, where another worktree's Django may
  // answer: nothing listens on this port, and specs stub the socket (e2e/graphql-stub.ts).
  NEXT_PUBLIC_GRAPHQL_WS_URL: `ws://localhost:${apiStubPort + 1}/graphql`,
  // The stubbed API has no real PaymentIntent, so no Stripe key, even if .env.local has
  // one for manual test-mode runs (e2e/checkout).
  NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY: '',
  // Placeholder signing identities so the app association files are served (e2e/app-links),
  // and a placeholder Google client ID so the sign-in page offers Google (e2e/sign-in, which
  // stubs Google's script).
  NEXT_PUBLIC_GOOGLE_WEB_CLIENT_ID: 'test-client.apps.googleusercontent.com',
  APPLE_TEAM_ID: 'TESTTEAM01',
  ANDROID_SHA256_CERT_FINGERPRINTS: Array.from({ length: 32 }, () => 'AB').join(':'),
};

export default defineConfig({
  testDir: './e2e',
  forbidOnly: !!process.env.CI,
  use: { baseURL: `http://localhost:${port}` },
  projects: [
    {
      name: 'mobile',
      testIgnore: devSmoke,
      use: { ...devices['Pixel 7'], viewport: { width: 375, height: 812 } },
    },
    {
      name: 'desktop',
      testIgnore: devSmoke,
      use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 800 } },
    },
    {
      name: 'dev-server',
      testMatch: devSmoke,
      use: { ...devices['Desktop Chrome'], baseURL: `http://localhost:${devPort}` },
    },
  ],
  // Playwright starts these in order, each once the one before is ready: the API stub, then the
  // production build (its static pages read the stub), then the dev server.
  webServer: [
    {
      // The API that server components call (the legal pages); see e2e/legal-api-stub.mjs.
      command: `node e2e/legal-api-stub.mjs`,
      port: apiStubPort,
      env: { LEGAL_API_STUB_PORT: String(apiStubPort) },
      reuseExistingServer: !process.env.CI,
    },
    {
      // The specs run against a production build, as Vercel serves it. `next dev` compiles each
      // route on its first request, which made a client-side redirect to a route no earlier spec
      // had opened miss its 5 s assertion on CI (BEAV-206).
      command: `pnpm build && pnpm start --port ${port}`,
      url: `http://localhost:${port}/hr`,
      reuseExistingServer: !process.env.CI,
      timeout: 300_000,
      // PORT too, for the canonical URLs of pages prerendered during the build (src/i18n/metadata.ts).
      env: { ...appEnv, PORT: String(port) },
    },
    {
      // The same command developers run, so e2e/dev-server.spec.ts proves
      // `pnpm --filter @taskbeavo/web dev` serves the home page.
      command: `pnpm dev --port ${devPort}`,
      url: `http://localhost:${devPort}/hr`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: { ...appEnv, PORT: String(devPort) },
    },
  ],
});

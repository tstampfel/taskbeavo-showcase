import { defineConfig, devices } from '@playwright/test';

const port = 3100;
const apiStubPort = 3199;

export default defineConfig({
  testDir: './e2e',
  forbidOnly: !!process.env.CI,
  use: { baseURL: `http://localhost:${port}` },
  projects: [
    {
      name: 'mobile',
      use: { ...devices['Pixel 7'], viewport: { width: 375, height: 812 } },
    },
    {
      name: 'desktop',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 800 } },
    },
  ],
  webServer: [
    {
      // The API that server components call (the legal pages); see e2e/legal-api-stub.mjs.
      command: `node e2e/legal-api-stub.mjs`,
      port: apiStubPort,
      env: { LEGAL_API_STUB_PORT: String(apiStubPort) },
      reuseExistingServer: !process.env.CI,
    },
    {
      // The same command developers run, so this spec proves `pnpm --filter @taskbeavo/web dev` serves the home page.
      command: `pnpm dev --port ${port}`,
      url: `http://localhost:${port}/hr`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      // Placeholder signing identities so the app association files are served (e2e/app-links),
      // and a placeholder Google client ID so the sign-in page offers Google (e2e/sign-in, which
      // stubs Google's script).
      env: {
        GRAPHQL_URL: `http://localhost:${apiStubPort}/graphql`,
        // Never the default ws://localhost:8000/graphql, where another worktree's Django may
        // answer: nothing listens on this port, and specs stub the socket (e2e/graphql-stub.ts).
        NEXT_PUBLIC_GRAPHQL_WS_URL: `ws://localhost:${apiStubPort + 1}/graphql`,
        // The stubbed API has no real PaymentIntent, so no Stripe key, even if .env.local has
        // one for manual test-mode runs (e2e/checkout).
        NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY: '',
        NEXT_PUBLIC_GOOGLE_WEB_CLIENT_ID: 'test-client.apps.googleusercontent.com',
        APPLE_TEAM_ID: 'TESTTEAM01',
        ANDROID_SHA256_CERT_FINGERPRINTS: Array.from({ length: 32 }, () => 'AB').join(':'),
      },
    },
  ],
});

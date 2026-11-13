import { afterEach, describe, expect, it, vi } from 'vitest';

async function buildOptions(env: Record<string, string | undefined>) {
  vi.resetModules();
  for (const [name, value] of Object.entries(env)) vi.stubEnv(name, value);
  return (await import('./next.config')).sentryBuildOptions;
}

afterEach(() => vi.unstubAllEnvs());

describe('Sentry source maps', () => {
  it("upload to Sentry's EU region with the build's token and are not served afterwards", async () => {
    const options = await buildOptions({
      SENTRY_AUTH_TOKEN: 'sntrys_x',
      SENTRY_ORG: 'taskbeavo',
      SENTRY_PROJECT: 'web',
    });
    expect(options).toMatchObject({
      org: 'taskbeavo',
      project: 'web',
      authToken: 'sntrys_x',
      sentryUrl: 'https://de.sentry.io/',
      sourcemaps: { disable: false, deleteSourcemapsAfterUpload: true },
    });
  });

  it('are not uploaded without a token', async () => {
    const options = await buildOptions({ SENTRY_AUTH_TOKEN: '' });
    expect(options.sourcemaps.disable).toBe('disable-upload');
  });
});

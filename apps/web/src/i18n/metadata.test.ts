import { afterEach, describe, expect, it, vi } from 'vitest';
import { localeAlternates, siteUrl } from './metadata';

describe('localeAlternates', () => {
  it('links every locale plus x-default to Croatian', () => {
    expect(localeAlternates('de', '/tasks')).toEqual({
      canonical: '/de/tasks',
      languages: {
        hr: '/hr/tasks',
        en: '/en/tasks',
        de: '/de/tasks',
        fr: '/fr/tasks',
        'x-default': '/hr/tasks',
      },
    });
  });
});

describe('siteUrl', () => {
  afterEach(() => vi.unstubAllEnvs());

  it('prefers NEXT_PUBLIC_SITE_URL', () => {
    vi.stubEnv('NEXT_PUBLIC_SITE_URL', 'https://taskbeavo.hr');
    vi.stubEnv('VERCEL_PROJECT_PRODUCTION_URL', 'taskbeavo.vercel.app');
    expect(siteUrl().href).toBe('https://taskbeavo.hr/');
  });

  it("falls back to Vercel's production domain", () => {
    vi.stubEnv('NEXT_PUBLIC_SITE_URL', '');
    vi.stubEnv('VERCEL_PROJECT_PRODUCTION_URL', 'taskbeavo.vercel.app');
    expect(siteUrl().href).toBe('https://taskbeavo.vercel.app/');
  });
});

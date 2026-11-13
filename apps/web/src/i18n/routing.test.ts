import { describe, expect, it } from 'vitest';
import { routing } from './routing';

describe('routing', () => {
  it('serves hr, en, de and fr with Croatian as default', () => {
    expect(routing.locales).toEqual(['hr', 'en', 'de', 'fr']);
    expect(routing.defaultLocale).toBe('hr');
  });

  it('always prefixes the locale so every page has one canonical URL per language', () => {
    expect(routing.localePrefix).toBe('always');
  });

  it('remembers the chosen language in a cookie that outlives the browser session', () => {
    expect(routing.localeCookie).toEqual({ name: 'NEXT_LOCALE', maxAge: 60 * 60 * 24 * 365 });
  });
});

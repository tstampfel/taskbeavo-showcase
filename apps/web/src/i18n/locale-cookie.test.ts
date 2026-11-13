import { describe, expect, it } from 'vitest';
import { accountLocaleCookie } from './locale-cookie';

describe('accountLocaleCookie', () => {
  it('stores the account language for a year in the cookie next-intl reads', () => {
    expect(accountLocaleCookie('FR')).toEqual({
      name: 'NEXT_LOCALE',
      value: 'fr',
      options: { sameSite: 'lax', path: '/', maxAge: 60 * 60 * 24 * 365 },
    });
  });

  it('falls back to Croatian for a language the web app does not serve', () => {
    expect(accountLocaleCookie('IT').value).toBe('hr');
  });
});

import { NextRequest, NextResponse } from 'next/server';
import { describe, expect, it } from 'vitest';
import { withoutLocaleCookieWrite } from './i18n/locale-cookie';
import proxy from './proxy';

function visit(path: string, headers: Record<string, string> = {}) {
  return proxy(
    new NextRequest(`https://taskbeavo.test${path}`, {
      headers: { 'sec-fetch-dest': 'document', ...headers },
    }),
  );
}

describe('proxy', () => {
  it('redirects / by Accept-Language without setting the locale cookie', () => {
    const response = visit('/', { 'accept-language': 'de-DE,de;q=0.9' });

    expect(response.headers.get('location')).toBe('https://taskbeavo.test/de');
    expect(response.headers.getSetCookie()).toEqual([]);
  });

  it('does not set the locale cookie when a page of another language is visited', () => {
    const response = visit('/fr/fees', { 'accept-language': 'hr' });

    expect(response.headers.getSetCookie()).toEqual([]);
  });

  it('does not overwrite a language the user picked when they open a shared link', () => {
    const response = visit('/en/fees', { cookie: 'NEXT_LOCALE=de' });

    expect(response.headers.getSetCookie()).toEqual([]);
  });

  it('still redirects / to the language the user picked', () => {
    const response = visit('/', { cookie: 'NEXT_LOCALE=fr', 'accept-language': 'de' });

    expect(response.headers.get('location')).toBe('https://taskbeavo.test/fr');
  });
});

describe('withoutLocaleCookieWrite', () => {
  it('keeps every other cookie', () => {
    const response = NextResponse.next();
    response.cookies.set('NEXT_LOCALE', 'de');
    response.cookies.set('other', '1');

    const cookies = withoutLocaleCookieWrite(response).headers.getSetCookie();

    expect(cookies).toHaveLength(1);
    expect(cookies[0]).toMatch(/^other=1/);
  });
});

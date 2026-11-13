import { afterEach, describe, expect, it, vi } from 'vitest';
import { clientIp } from './client-ip';
import { CSRF_HEADER, isSameOriginRequest } from './csrf';
import {
  ACCESS_COOKIE,
  REFRESH_COOKIE,
  accessTokenFor,
  clearedSessionCookies,
  sessionCookies,
} from './session';
import { selectsTokenField } from './token-operations';
import {
  reportMissingProxySecret,
  resetMissingProxySecretReport,
  upstreamHeaders,
} from './upstream';

function sameOrigin(overrides: Record<string, string | null> = {}) {
  const headers = new Headers({
    'content-type': 'application/json',
    [CSRF_HEADER]: '1',
    origin: 'https://taskbeavo.hr',
    host: 'taskbeavo.hr',
    'sec-fetch-site': 'same-origin',
  });
  for (const [name, value] of Object.entries(overrides)) {
    if (value === null) headers.delete(name);
    else headers.set(name, value);
  }
  return { method: 'POST', headers };
}

describe('isSameOriginRequest', () => {
  it('accepts a JSON POST from our own page', () => {
    expect(isSameOriginRequest(sameOrigin())).toBe(true);
  });

  it('accepts browsers that send no Sec-Fetch-Site', () => {
    expect(isSameOriginRequest(sameOrigin({ 'sec-fetch-site': null }))).toBe(true);
  });

  it.each([
    ['another site', { origin: 'https://evil.example', 'sec-fetch-site': 'cross-site' }],
    ['a sibling subdomain', { origin: 'https://evil.taskbeavo.hr', 'sec-fetch-site': 'same-site' }],
    ['no CSRF header', { [CSRF_HEADER]: null }],
    ['a form post', { 'content-type': 'application/x-www-form-urlencoded' }],
    ['no Origin', { origin: null }],
    ['a malformed Origin', { origin: 'null' }],
  ])('refuses %s', (_label, overrides) => {
    expect(isSameOriginRequest(sameOrigin(overrides))).toBe(false);
  });

  it('refuses GET', () => {
    expect(isSameOriginRequest({ ...sameOrigin(), method: 'GET' })).toBe(false);
  });
});

describe('clientIp', () => {
  it("prefers Vercel's X-Real-IP", () => {
    expect(
      clientIp(new Headers({ 'x-real-ip': '203.0.113.7', 'x-forwarded-for': '10.0.0.1' })),
    ).toBe('203.0.113.7');
  });

  it('falls back to the first X-Forwarded-For entry', () => {
    expect(clientIp(new Headers({ 'x-forwarded-for': '203.0.113.7, 10.0.0.1' }))).toBe(
      '203.0.113.7',
    );
  });

  it('is null without either header', () => {
    expect(clientIp(new Headers())).toBeNull();
  });
});

describe('selectsTokenField', () => {
  it.each([
    'mutation { verifyEmailCode(email: "a", code: "1", device: { platform: WEB }) { __typename } }',
    'mutation { x: refreshSession(refreshToken: "t") { id } }',
    'mutation { ...M } fragment M on Mutation { signOut(refreshToken: "t") }',
    'mutation { ... on Mutation { completeAccountDetails { id } } }',
    'mutation { signInWithProvider(input: {}, device: { platform: WEB }) { __typename } }',
    'mutation { linkProviderWithEmailCode(ticket: "t", code: "1", device: { platform: WEB }) { id } }',
    'mutation { signOutEverywhere }',
  ])('finds a token field in %s', (query) => {
    expect(selectsTokenField(query)).toBe(true);
  });

  it('lets other operations through', () => {
    expect(selectsTokenField('mutation { requestEmailCode(email: "a", locale: EN) }')).toBe(false);
    expect(selectsTokenField('mutation { webSocketToken }')).toBe(false);
    expect(selectsTokenField('mutation { requestSignInNonce { nonce } }')).toBe(false);
    expect(selectsTokenField('{ viewer { id } }')).toBe(false);
  });

  it('leaves unparseable queries to the API', () => {
    expect(selectsTokenField('{ not graphql')).toBe(false);
  });
});

describe('upstreamHeaders', () => {
  const request = {
    headers: new Headers({ 'x-real-ip': '203.0.113.7', traceparent: '00-abc-def-01' }),
  };

  it('sends the access token as a bearer token and passes the trace context', () => {
    const headers = upstreamHeaders(request, 'jwt', {});
    expect(headers.get('authorization')).toBe('Bearer jwt');
    expect(headers.get('traceparent')).toBe('00-abc-def-01');
  });

  it("sends the visitor's address with the shared secret", () => {
    const headers = upstreamHeaders(request, undefined, { WEB_PROXY_SECRET: 's3cret' });
    expect(headers.get('authorization')).toBeNull();
    expect(headers.get('x-taskbeavo-client-ip')).toBe('203.0.113.7');
    expect(headers.get('x-taskbeavo-proxy-secret')).toBe('s3cret');
  });

  it('sends no address without the secret', () => {
    const headers = upstreamHeaders(request, undefined, {});
    expect(headers.get('x-taskbeavo-client-ip')).toBeNull();
    expect(headers.get('x-taskbeavo-proxy-secret')).toBeNull();
  });
});

const issued = {
  accessToken: 'access-2',
  accessTokenExpiresAt: '2026-10-29T12:15:00+00:00',
  refreshToken: 'refresh-2',
  refreshTokenExpiresAt: '2026-11-28T12:00:00+00:00',
};

describe('sessionCookies', () => {
  it('stores both tokens in httpOnly, SameSite=Lax, Secure cookies', () => {
    const cookies = sessionCookies(issued, { NODE_ENV: 'production' });
    expect(cookies.map((c) => [c.name, c.value])).toEqual([
      [ACCESS_COOKIE, 'access-2'],
      [REFRESH_COOKIE, 'refresh-2'],
    ]);
    for (const { options } of cookies) {
      expect(options).toMatchObject({ httpOnly: true, secure: true, sameSite: 'lax', path: '/' });
    }
  });

  it('expires the access cookie before its token', () => {
    const [access, refresh] = sessionCookies(issued, {});
    expect(access?.options.expires.toISOString()).toBe('2026-10-29T12:14:30.000Z');
    expect(refresh?.options.expires.toISOString()).toBe('2026-11-28T12:00:00.000Z');
  });

  it('is not Secure on a local http dev server', () => {
    expect(sessionCookies(issued, { NODE_ENV: 'development' })[0]?.options.secure).toBe(false);
  });

  it('clears both cookies on sign-out', () => {
    expect(clearedSessionCookies({}).map((c) => [c.name, c.value, c.options.expires])).toEqual([
      [ACCESS_COOKIE, '', new Date(0)],
      [REFRESH_COOKIE, '', new Date(0)],
    ]);
  });
});

function withCookies(cookies: Record<string, string>) {
  return {
    headers: new Headers(),
    cookies: {
      get: (name: string) => (name in cookies ? { value: cookies[name] as string } : undefined),
    },
  };
}

function apiAnswers(body: unknown) {
  const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => Response.json(body));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

describe('accessTokenFor', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('uses the access cookie without calling the API', async () => {
    const fetchMock = apiAnswers({});
    const result = await accessTokenFor(
      withCookies({ [ACCESS_COOKIE]: 'a', [REFRESH_COOKIE]: 'r' }),
    );
    expect(result).toEqual({ accessToken: 'a', cookies: [] });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('is signed out without cookies', async () => {
    expect(await accessTokenFor(withCookies({}))).toEqual({ cookies: [] });
  });

  it('refreshes when only the refresh cookie is left', async () => {
    const fetchMock = apiAnswers({
      data: { refreshSession: { ...issued, id: 's1', user: { id: '1' } } },
    });
    const result = await accessTokenFor(withCookies({ [REFRESH_COOKIE]: 'refresh-1' }));
    expect(result.accessToken).toBe('access-2');
    expect(result.cookies.map((c) => c.value)).toEqual(['access-2', 'refresh-2']);
    const sent = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body)) as {
      variables: unknown;
    };
    expect(sent.variables).toEqual({ refreshToken: 'refresh-1' });
  });

  it('clears the cookies when the session has ended', async () => {
    apiAnswers({ data: null, errors: [{ message: 'x', extensions: { code: 'SESSION_EXPIRED' } }] });
    const result = await accessTokenFor(withCookies({ [REFRESH_COOKIE]: 'reused' }));
    expect(result.accessToken).toBeUndefined();
    expect(result.cookies.map((c) => c.value)).toEqual(['', '']);
  });

  it('keeps the cookies when the API is unreachable', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => Promise.reject(new TypeError('fetch failed'))),
    );
    expect(await accessTokenFor(withCookies({ [REFRESH_COOKIE]: 'r' }))).toEqual({ cookies: [] });
  });
});

describe('reportMissingProxySecret', () => {
  afterEach(() => resetMissingProxySecretReport());

  it('logs once when a production server has no WEB_PROXY_SECRET', () => {
    const log = vi.fn();
    reportMissingProxySecret({ NODE_ENV: 'production' }, log);
    reportMissingProxySecret({ NODE_ENV: 'production' }, log);
    expect(log).toHaveBeenCalledTimes(1);
    expect(log.mock.calls[0]?.[0]).toContain('WEB_PROXY_SECRET is not set');
  });

  it('stays quiet with the secret set, and on the dev server the e2e tests use', () => {
    const log = vi.fn();
    reportMissingProxySecret({ NODE_ENV: 'production', WEB_PROXY_SECRET: 's' }, log);
    reportMissingProxySecret({ NODE_ENV: 'development' }, log);
    expect(log).not.toHaveBeenCalled();
  });
});

import { NextRequest } from 'next/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ACCESS_COOKIE, DEVICE_COOKIE, REFRESH_COOKIE } from '../../auth/session';
import { POST as completeAccountDetails } from './auth/complete-account-details/route';
import { POST as linkProvider } from './auth/link-provider/route';
import { POST as signInWithProvider } from './auth/sign-in-with-provider/route';
import { POST as signOutEverywhere } from './auth/sign-out-everywhere/route';
import { POST as signOut } from './auth/sign-out/route';
import { POST as verifyEmailCode } from './auth/verify-email-code/route';
import { POST as graphql } from './graphql/route';

const ORIGIN = 'https://taskbeavo.hr';

function post(
  path: string,
  body: unknown,
  {
    cookies = {},
    headers = {},
  }: { cookies?: Record<string, string>; headers?: Record<string, string> } = {},
) {
  const cookieHeader = Object.entries(cookies)
    .map(([name, value]) => `${name}=${value}`)
    .join('; ');
  return new NextRequest(`${ORIGIN}${path}`, {
    method: 'POST',
    body: JSON.stringify(body),
    headers: {
      'content-type': 'application/json',
      'x-taskbeavo-csrf': '1',
      origin: ORIGIN,
      host: 'taskbeavo.hr',
      'x-real-ip': '203.0.113.7',
      ...(cookieHeader ? { cookie: cookieHeader } : {}),
      ...headers,
    },
  });
}

/** Stubs Django: answers each call with the next body, records what was sent. */
function api(...bodies: unknown[]) {
  const calls: {
    headers: Headers;
    body: { query: string; variables?: Record<string, unknown> };
  }[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (_url: string, init: RequestInit) => {
      calls.push({ headers: new Headers(init.headers), body: JSON.parse(String(init.body)) });
      return Response.json(bodies.shift() ?? {});
    }),
  );
  return calls;
}

const session = {
  __typename: 'AuthSession',
  id: 's1',
  accessToken: 'access-1',
  accessTokenExpiresAt: '2026-10-29T12:15:00+00:00',
  refreshToken: 'refresh-1',
  refreshTokenExpiresAt: '2026-11-28T12:00:00+00:00',
  user: { id: '7', email: 'ana@example.com', locale: 'DE' },
};

function setCookies(response: Response): Record<string, string> {
  return Object.fromEntries(
    response.headers.getSetCookie().map((line) => {
      const [pair = ''] = line.split(';');
      const [name = '', value = ''] = pair.split('=');
      return [name, value];
    }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe('/api/graphql', () => {
  it('forwards with the access cookie as a bearer token and the visitor address', async () => {
    vi.stubEnv('WEB_PROXY_SECRET', 's3cret');
    const calls = api({ data: { viewer: { id: '7' } } });

    const response = await graphql(
      post('/api/graphql', { query: '{ viewer { id } }' }, { cookies: { [ACCESS_COOKIE]: 'a1' } }),
    );

    expect(await response.json()).toEqual({ data: { viewer: { id: '7' } } });
    expect(calls).toHaveLength(1);
    expect(calls[0]?.headers.get('authorization')).toBe('Bearer a1');
    expect(calls[0]?.headers.get('x-taskbeavo-client-ip')).toBe('203.0.113.7');
    expect(calls[0]?.headers.get('x-taskbeavo-proxy-secret')).toBe('s3cret');
  });

  it('refreshes the session first when the access cookie has expired', async () => {
    const calls = api({ data: { refreshSession: session } }, { data: { viewer: { id: '7' } } });

    const response = await graphql(
      post('/api/graphql', { query: '{ viewer { id } }' }, { cookies: { [REFRESH_COOKIE]: 'r0' } }),
    );

    expect(calls[0]?.body.variables).toEqual({ refreshToken: 'r0' });
    expect(calls[1]?.headers.get('authorization')).toBe('Bearer access-1');
    expect(setCookies(response)).toMatchObject({
      [ACCESS_COOKIE]: 'access-1',
      [REFRESH_COOKIE]: 'refresh-1',
    });
    expect(response.headers.getSetCookie().join('\n')).toContain('HttpOnly');
  });

  it('refuses a cross-site request without calling the API', async () => {
    const calls = api();
    const response = await graphql(
      post(
        '/api/graphql',
        { query: '{ viewer { id } }' },
        { headers: { origin: 'https://evil.example' } },
      ),
    );
    expect(response.status).toBe(403);
    expect((await response.json()).errors[0].extensions.code).toBe('REQUEST_REJECTED');
    expect(calls).toHaveLength(0);
  });

  it('refuses token-returning mutations, which must go through /api/auth', async () => {
    const calls = api();
    const response = await graphql(
      post('/api/graphql', {
        query: 'mutation { refreshSession(refreshToken: "x") { accessToken } }',
      }),
    );
    expect(response.status).toBe(403);
    expect(calls).toHaveLength(0);
  });
});

describe('/api/auth/sign-in-with-provider', () => {
  const body = { provider: 'APPLE', idToken: 'id-token', nonce: 'n', firstName: 'Ana' };

  it('sends the device cookie back and keeps the device token in it, not the page', async () => {
    const calls = api({ data: { signInWithProvider: { ...session, deviceToken: 'device-2' } } });

    const response = await signInWithProvider(
      post('/api/auth/sign-in-with-provider', body, { cookies: { [DEVICE_COOKIE]: 'device-1' } }),
    );

    expect(calls[0]?.body.variables?.device).toEqual({
      platform: 'WEB',
      name: '',
      deviceToken: 'device-1',
    });
    expect(setCookies(response)[DEVICE_COOKIE]).toBe('device-2');
    const deviceLine = response.headers
      .getSetCookie()
      .find((line) => line.startsWith(`${DEVICE_COOKIE}=`));
    // Six months, renewed by this sign-in; httpOnly like the session cookies.
    expect(deviceLine).toContain(`Max-Age=${183 * 24 * 60 * 60}`);
    expect(deviceLine).toContain('HttpOnly');
    expect(JSON.stringify(await response.json())).not.toContain('device-2');
  });

  it('keeps the tokens in httpOnly cookies and gives the page only the user', async () => {
    const calls = api({ data: { signInWithProvider: session } });

    const response = await signInWithProvider(post('/api/auth/sign-in-with-provider', body));

    const json = await response.json();
    expect(json).toEqual({
      data: { signInWithProvider: { __typename: 'AuthSession', id: 's1', user: session.user } },
    });
    expect(JSON.stringify(json)).not.toContain('refresh-1');
    expect(setCookies(response)).toMatchObject({
      [ACCESS_COOKIE]: 'access-1',
      [REFRESH_COOKIE]: 'refresh-1',
    });
    expect(calls[0]?.body.variables).toEqual({
      input: { provider: 'APPLE', idToken: 'id-token', nonce: 'n', firstName: 'Ana', lastName: '' },
      device: { platform: 'WEB', name: '' },
    });
  });

  it('passes the account-details ticket of a new account through', async () => {
    const required = {
      __typename: 'AccountDetailsRequired',
      id: 'd',
      email: 'x7k2p9@privaterelay.example.com',
      ticket: 't',
      firstName: 'Ana',
      lastName: null,
    };
    api({ data: { signInWithProvider: required } });

    const response = await signInWithProvider(post('/api/auth/sign-in-with-provider', body));

    expect(await response.json()).toEqual({ data: { signInWithProvider: required } });
    expect(response.headers.getSetCookie()).toEqual([]);
  });

  it('passes the link ticket of an existing account through without cookies', async () => {
    const required = {
      __typename: 'ProviderLinkRequired',
      id: 'l',
      email: 'ana@example.com',
      ticket: 't',
    };
    api({ data: { signInWithProvider: required } });

    const response = await signInWithProvider(post('/api/auth/sign-in-with-provider', body));

    expect(await response.json()).toEqual({ data: { signInWithProvider: required } });
    expect(response.headers.getSetCookie()).toEqual([]);
  });

  it('refuses an unknown provider without calling the API', async () => {
    const calls = api();

    const response = await signInWithProvider(
      post('/api/auth/sign-in-with-provider', { ...body, provider: 'FACEBOOK' }),
    );

    expect(response.status).toBe(403);
    expect(calls).toHaveLength(0);
  });

  it('refuses a cross-site request', async () => {
    const response = await signInWithProvider(
      post('/api/auth/sign-in-with-provider', body, { headers: { 'x-taskbeavo-csrf': '' } }),
    );
    expect(response.status).toBe(403);
  });
});

describe('/api/auth/link-provider', () => {
  it('keeps the tokens in httpOnly cookies and gives the page only the user', async () => {
    const calls = api({ data: { linkProviderWithEmailCode: session } });

    const response = await linkProvider(
      post('/api/auth/link-provider', { ticket: 't', code: '123456' }),
    );

    const json = await response.json();
    expect(json).toEqual({
      data: {
        linkProviderWithEmailCode: { __typename: 'AuthSession', id: 's1', user: session.user },
      },
    });
    expect(JSON.stringify(json)).not.toContain('refresh-1');
    expect(setCookies(response)).toMatchObject({
      [ACCESS_COOKIE]: 'access-1',
      [REFRESH_COOKIE]: 'refresh-1',
    });
    expect(calls[0]?.body.variables).toEqual({
      ticket: 't',
      code: '123456',
      device: { platform: 'WEB', name: '' },
    });
  });

  it('passes error codes through', async () => {
    const errors = [{ message: 'Wrong code.', extensions: { code: 'INVALID_CODE' } }];
    api({ data: null, errors });

    const response = await linkProvider(
      post('/api/auth/link-provider', { ticket: 't', code: '000000' }),
    );

    expect(await response.json()).toEqual({ data: null, errors });
    expect(response.headers.getSetCookie()).toEqual([]);
  });

  it('refuses a cross-site request', async () => {
    const response = await linkProvider(
      post(
        '/api/auth/link-provider',
        { ticket: 't', code: '1' },
        { headers: { 'x-taskbeavo-csrf': '' } },
      ),
    );
    expect(response.status).toBe(403);
  });
});

describe('/api/auth/verify-email-code', () => {
  it('keeps the tokens in httpOnly cookies and gives the page only the user', async () => {
    const calls = api({ data: { verifyEmailCode: session } });

    const response = await verifyEmailCode(
      post('/api/auth/verify-email-code', { email: 'ana@example.com', code: '123456' }),
    );

    const body = await response.json();
    expect(body).toEqual({
      data: { verifyEmailCode: { __typename: 'AuthSession', id: 's1', user: session.user } },
    });
    expect(JSON.stringify(body)).not.toContain('refresh-1');
    expect(setCookies(response)).toMatchObject({
      [ACCESS_COOKIE]: 'access-1',
      [REFRESH_COOKIE]: 'refresh-1',
      NEXT_LOCALE: 'de',
    });
    expect(calls[0]?.body.variables).toEqual({
      email: 'ana@example.com',
      code: '123456',
      device: { platform: 'WEB', name: '' },
    });
  });

  it('passes the account-details ticket of a new address through', async () => {
    const required = {
      __typename: 'AccountDetailsRequired',
      id: 'd',
      email: 'new@example.com',
      ticket: 't',
    };
    api({ data: { verifyEmailCode: required } });

    const response = await verifyEmailCode(
      post('/api/auth/verify-email-code', { email: 'new@example.com', code: '123456' }),
    );

    expect(await response.json()).toEqual({ data: { verifyEmailCode: required } });
    expect(response.headers.getSetCookie()).toEqual([]);
  });

  it('passes error codes through', async () => {
    const errors = [{ message: 'Wrong code.', extensions: { code: 'INVALID_CODE' } }];
    api({ data: null, errors });

    const response = await verifyEmailCode(
      post('/api/auth/verify-email-code', { email: 'ana@example.com', code: '000000' }),
    );

    expect(await response.json()).toEqual({ data: null, errors });
  });

  it('refuses a cross-site request', async () => {
    const response = await verifyEmailCode(
      post(
        '/api/auth/verify-email-code',
        { email: 'a', code: 'b' },
        { headers: { 'x-taskbeavo-csrf': '' } },
      ),
    );
    expect(response.status).toBe(403);
  });
});

describe('/api/auth/complete-account-details', () => {
  it('creates the account and stores the session in cookies', async () => {
    const calls = api({ data: { completeAccountDetails: session } });
    const details = {
      ticket: 't',
      firstName: 'Ana',
      lastName: 'Horvat',
      isAdult: true,
      locale: 'DE',
      country: 'DE',
      timeZone: 'Europe/Berlin',
    };

    const response = await completeAccountDetails(
      post('/api/auth/complete-account-details', { details, deviceName: 'Firefox on Mac' }),
    );

    expect((await response.json()).data.completeAccountDetails.user).toEqual(session.user);
    expect(setCookies(response)[REFRESH_COOKIE]).toBe('refresh-1');
    expect(setCookies(response).NEXT_LOCALE).toBe('de');
    expect(calls[0]?.body.variables).toEqual({
      details,
      device: { platform: 'WEB', name: 'Firefox on Mac' },
    });
  });

  it('passes UNDERAGE through without setting cookies', async () => {
    const errors = [{ message: '18+', extensions: { code: 'UNDERAGE' } }];
    api({ data: null, errors });

    const response = await completeAccountDetails(
      post('/api/auth/complete-account-details', { details: { isAdult: false } }),
    );

    expect((await response.json()).errors).toEqual(errors);
    expect(response.headers.getSetCookie()).toEqual([]);
  });
});

describe('/api/auth/sign-out', () => {
  it('revokes the session and clears the cookies', async () => {
    const calls = api({ data: { signOut: true } });

    const response = await signOut(
      post('/api/auth/sign-out', {}, { cookies: { [ACCESS_COOKIE]: 'a', [REFRESH_COOKIE]: 'r' } }),
    );

    expect(calls[0]?.body.variables).toEqual({ refreshToken: 'r' });
    expect(setCookies(response)).toEqual({ [ACCESS_COOKIE]: '', [REFRESH_COOKIE]: '' });
  });

  it('clears the cookies even when the API is unreachable', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => Promise.reject(new TypeError('fetch failed'))),
    );

    const response = await signOut(
      post('/api/auth/sign-out', {}, { cookies: { [REFRESH_COOKIE]: 'r' } }),
    );

    expect(response.status).toBe(200);
    expect(setCookies(response)).toEqual({ [ACCESS_COOKIE]: '', [REFRESH_COOKIE]: '' });
  });
});

describe('/api/auth/sign-out-everywhere', () => {
  const request = () =>
    post(
      '/api/auth/sign-out-everywhere',
      {},
      { cookies: { [ACCESS_COOKIE]: 'a', [REFRESH_COOKIE]: 'r' } },
    );

  it('logs out of all devices with the access token and clears the cookies', async () => {
    const calls = api({ data: { signOutEverywhere: true } });

    const response = await signOutEverywhere(request());

    expect(calls[0]?.headers.get('authorization')).toBe('Bearer a');
    expect(calls[0]?.body.query).toContain('signOutEverywhere');
    expect(await response.json()).toEqual({ data: { signOutEverywhere: true } });
    // The device cookie stays: this browser remains a known device.
    expect(setCookies(response)).toEqual({ [ACCESS_COOKIE]: '', [REFRESH_COOKIE]: '' });
  });

  it('reports an unreachable API and keeps the cookies', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => Promise.reject(new TypeError('fetch failed'))),
    );

    const response = await signOutEverywhere(request());

    expect(response.status).toBe(502);
    expect(response.headers.getSetCookie()).toEqual([]);
  });

  it('clears the cookies of a session that already ended', async () => {
    const errors = [{ message: 'Sign in first.', extensions: { code: 'UNAUTHENTICATED' } }];
    api({ errors });

    const response = await signOutEverywhere(request());

    expect((await response.json()).errors).toEqual(errors);
    expect(setCookies(response)).toEqual({ [ACCESS_COOKIE]: '', [REFRESH_COOKIE]: '' });
  });

  it('refuses cross-site requests', async () => {
    const calls = api();

    const response = await signOutEverywhere(
      post('/api/auth/sign-out-everywhere', {}, { headers: { origin: 'https://evil.example' } }),
    );

    expect(response.status).toBe(403);
    expect(calls).toEqual([]);
  });
});

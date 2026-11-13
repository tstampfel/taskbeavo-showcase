import { SignInError, signInErrorOf } from '@taskbeavo/graphql';
import { describe, expect, it, vi } from 'vitest';
import { browserDeviceName, createAuthClient, safeNextPath } from './auth-client';

const user = {
  id: 'u1',
  email: 'ana@example.com',
  firstName: 'Ana',
  lastName: 'Kovač',
  locale: 'DE',
  country: 'DE',
  timeZone: 'Europe/Berlin',
  phoneVerified: false,
  phoneNumberMasked: null,
};

function respond(body: unknown) {
  return vi.fn<typeof fetch>(async () => new Response(JSON.stringify(body)));
}

describe('createAuthClient', () => {
  it('posts to the route handler with the CSRF header and the device name', async () => {
    const fetchImpl = respond({
      data: { verifyEmailCode: { __typename: 'AuthSession', id: 's1', user } },
    });
    const client = createAuthClient(fetchImpl, 'Firefox on Mac');

    const answer = await client.verifyEmailCode('ana@example.com', '482107');

    expect(answer).toEqual({ __typename: 'AuthSession', id: 's1', user });
    const [path, init] = fetchImpl.mock.calls[0]!;
    expect(path).toBe('/api/auth/verify-email-code');
    expect(init?.method).toBe('POST');
    expect(init?.headers).toMatchObject({ 'x-taskbeavo-csrf': '1' });
    expect(JSON.parse(String(init?.body))).toEqual({
      email: 'ana@example.com',
      code: '482107',
      deviceName: 'Firefox on Mac',
    });
  });

  it('throws the API error code', async () => {
    const client = createAuthClient(
      respond({ data: null, errors: [{ message: 'no', extensions: { code: 'INVALID_CODE' } }] }),
    );
    const error = await client.verifyEmailCode('a@example.com', '000000').catch((e: unknown) => e);
    expect(error).toBeInstanceOf(SignInError);
    expect(signInErrorOf(error).code).toBe('INVALID_CODE');
  });

  it('throws a code-less error when the network fails', async () => {
    const client = createAuthClient(
      vi.fn<typeof fetch>(async () => Promise.reject(new TypeError())),
    );
    const error = await client.linkProvider('t', '1').catch((e: unknown) => e);
    expect(signInErrorOf(error)).toEqual({ code: undefined, retryAfterSeconds: undefined });
  });

  it('sends provider sign-ins, links and account details to their own handlers', async () => {
    const fetchImpl = vi.fn<typeof fetch>(async (path) => {
      const name = {
        '/api/auth/sign-in-with-provider': 'signInWithProvider',
        '/api/auth/link-provider': 'linkProviderWithEmailCode',
        '/api/auth/complete-account-details': 'completeAccountDetails',
      }[String(path)]!;
      return new Response(
        JSON.stringify({ data: { [name]: { __typename: 'AuthSession', id: 's', user } } }),
      );
    });
    const client = createAuthClient(fetchImpl, 'Safari on iOS');

    await client.signInWithProvider({
      provider: 'APPLE',
      idToken: 'jwt',
      nonce: 'n',
      firstName: 'Ana',
    });
    await client.linkProvider('ticket', '123456');
    await client.completeAccountDetails({
      ticket: 't',
      firstName: 'Ana',
      lastName: 'Kovač',
      isAdult: true,
      locale: 'DE',
      country: 'DE',
      timeZone: 'Europe/Berlin',
    });

    const bodies = fetchImpl.mock.calls.map(([, init]) => JSON.parse(String(init?.body)));
    expect(bodies[0]).toEqual({
      provider: 'APPLE',
      idToken: 'jwt',
      nonce: 'n',
      firstName: 'Ana',
      deviceName: 'Safari on iOS',
    });
    expect(bodies[1]).toEqual({ ticket: 'ticket', code: '123456', deviceName: 'Safari on iOS' });
    expect(bodies[2].details.country).toBe('DE');
  });

  it('signs out without throwing, even offline', async () => {
    const client = createAuthClient(
      vi.fn<typeof fetch>(async () => Promise.reject(new TypeError())),
    );
    await expect(client.signOut()).resolves.toBeUndefined();
  });
});

describe('browserDeviceName', () => {
  it('names the browser and the system', () => {
    expect(
      browserDeviceName(
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5; rv:131.0) Gecko/20100101 Firefox/131.0',
      ),
    ).toBe('Firefox on Mac');
    expect(
      browserDeviceName(
        'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1',
      ),
    ).toBe('Safari on iOS');
    expect(browserDeviceName('')).toBe('Browser');
  });
});

describe('safeNextPath', () => {
  it('keeps paths on this site, without their locale', () => {
    expect(safeNextPath('/bookings/12/release')).toBe('/bookings/12/release');
    expect(safeNextPath('/de/bookings/12')).toBe('/bookings/12');
    expect(safeNextPath('/de')).toBe('/');
    expect(safeNextPath('/design')).toBe('/design');
  });

  it('refuses anything that could leave the site', () => {
    expect(safeNextPath('https://evil.example')).toBe('/');
    expect(safeNextPath('//evil.example')).toBe('/');
    expect(safeNextPath('/\\evil.example')).toBe('/');
    expect(safeNextPath(null)).toBe('/');
    expect(safeNextPath('/de//evil.example')).toBe('/');
    expect(safeNextPath('/fr/\\evil.example')).toBe('/');
  });
});

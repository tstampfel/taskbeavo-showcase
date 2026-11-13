import { CurrentTermsDocument, VerifyEmailCodeDocument } from '@taskbeavo/graphql';
import { createSignInApi, type SignInClient } from '../src/auth/sign-in-api';
import { googleRedirectUri, googleClientId } from '../src/auth/providers/google';

const authSession = {
  __typename: 'AuthSession',
  id: 's1',
  accessToken: 'access',
  refreshToken: 'refresh',
  accessTokenExpiresAt: '2026-11-01T12:15:00Z',
  user: { id: 'u1' },
};

describe('createSignInApi', () => {
  it('asks the network, never the cache, for the terms version in force', async () => {
    const query = jest.fn(async () => ({
      data: { currentLegalDocument: { id: 'd1', version: '2026-11' } },
    }));
    const api = createSignInApi(
      { query } as unknown as SignInClient,
      { signedIn: jest.fn(async () => undefined) },
      { platform: 'IOS', name: 'iPhone' },
    );

    await expect(api.currentTermsVersion('FR')).resolves.toBe('2026-11');
    expect(query).toHaveBeenCalledWith({
      query: CurrentTermsDocument,
      variables: { language: 'FR' },
      fetchPolicy: 'network-only',
    });
    query.mockResolvedValueOnce({ data: { currentLegalDocument: null } } as never);
    await expect(api.currentTermsVersion('FR')).resolves.toBeNull();
  });

  it('keeps a returned session in the session store and sends the device', async () => {
    const order: string[] = [];
    const mutate = jest.fn(async () => ({ data: { verifyEmailCode: authSession } }));
    const signedIn = jest.fn(async () => void order.push('storeSession'));
    const resetStore = jest.fn(async () => (order.push('resetCache'), []));
    const device = { platform: 'IOS' as const, name: 'iPhone' };
    const api = createSignInApi(
      { mutate, resetStore } as unknown as SignInClient,
      { signedIn },
      device,
    );

    await api.verifyEmailCode('ana@example.com', '482107');

    expect(mutate).toHaveBeenCalledWith({
      mutation: VerifyEmailCodeDocument,
      variables: { email: 'ana@example.com', code: '482107', device },
    });
    expect(signedIn).toHaveBeenCalledWith(
      expect.objectContaining({ accessToken: 'access', refreshToken: 'refresh' }),
    );
    // The cached signed-out viewer goes only once the new tokens are in place.
    expect(order).toEqual(['storeSession', 'resetCache']);
  });

  it('stays signed in when the refetch after the reset fails offline', async () => {
    const mutate = jest.fn(async () => ({ data: { verifyEmailCode: authSession } }));
    const resetStore = jest.fn(async () => Promise.reject(new TypeError('offline')));
    const api = createSignInApi(
      { mutate, resetStore } as unknown as SignInClient,
      { signedIn: jest.fn(async () => undefined) },
      { platform: 'IOS', name: 'iPhone' },
    );
    await expect(api.verifyEmailCode('ana@example.com', '482107')).resolves.toMatchObject({
      __typename: 'AuthSession',
    });
  });

  it('stores nothing for an answer that is not a session', async () => {
    const mutate = jest.fn(async () => ({
      data: { verifyEmailCode: { __typename: 'AccountDetailsRequired', ticket: 't' } },
    }));
    const signedIn = jest.fn(async () => undefined);
    const resetStore = jest.fn(async () => []);
    const api = createSignInApi(
      { mutate, resetStore } as unknown as SignInClient,
      { signedIn },
      {
        platform: 'ANDROID',
        name: 'Android',
      },
    );

    await api.verifyEmailCode('new@example.com', '111111');

    expect(signedIn).not.toHaveBeenCalled();
    expect(resetStore).not.toHaveBeenCalled();
  });
});

describe('Google on the phone', () => {
  it('redirects to the reversed iOS client ID scheme', () => {
    expect(googleRedirectUri('123-abc.apps.googleusercontent.com')).toBe(
      'com.googleusercontent.apps.123-abc:/oauthredirect',
    );
  });

  it('asks with the iOS client on iOS and the web client (the token audience) on Android', () => {
    const env = {
      EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID: 'ios.apps.googleusercontent.com',
      EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID: 'web.apps.googleusercontent.com',
    };
    expect(googleClientId('ios', env)).toBe('ios.apps.googleusercontent.com');
    expect(googleClientId('android', env)).toBe('web.apps.googleusercontent.com');
    expect(googleClientId('ios', {})).toBeUndefined();
    expect(googleClientId('android', {})).toBeUndefined();
  });

  it('sends the kept device token and keeps the one a sign-in returns', async () => {
    const mutate = jest.fn(async () => ({
      data: { verifyEmailCode: { ...authSession, deviceToken: 'device-2' } },
    }));
    const deviceTokens = { load: jest.fn(async () => 'device-1'), save: jest.fn(async () => {}) };
    const api = createSignInApi(
      { mutate, resetStore: jest.fn(async () => []) } as unknown as SignInClient,
      { signedIn: jest.fn(async () => undefined) },
      { platform: 'IOS', name: 'iPhone' },
      deviceTokens,
    );

    await api.verifyEmailCode('ana@example.com', '482107');

    expect(mutate).toHaveBeenCalledWith({
      mutation: VerifyEmailCodeDocument,
      variables: {
        email: 'ana@example.com',
        code: '482107',
        device: { platform: 'IOS', name: 'iPhone', deviceToken: 'device-1' },
      },
    });
    expect(deviceTokens.save).toHaveBeenCalledWith('device-2');
  });

  it('signs in without a device token the first time', async () => {
    const mutate = jest.fn(async () => ({ data: { verifyEmailCode: authSession } }));
    const deviceTokens = { load: jest.fn(async () => null), save: jest.fn(async () => {}) };
    const api = createSignInApi(
      { mutate, resetStore: jest.fn(async () => []) } as unknown as SignInClient,
      { signedIn: jest.fn(async () => undefined) },
      { platform: 'IOS', name: 'iPhone' },
      deviceTokens,
    );

    await api.verifyEmailCode('ana@example.com', '482107');

    expect(mutate).toHaveBeenCalledWith(
      expect.objectContaining({
        variables: expect.objectContaining({ device: { platform: 'IOS', name: 'iPhone' } }),
      }),
    );
    expect(deviceTokens.save).not.toHaveBeenCalled();
  });
});

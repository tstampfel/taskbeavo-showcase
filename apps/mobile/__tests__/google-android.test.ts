import type * as Google from '../src/auth/providers/google';

jest.mock('react-native', () => ({ Platform: { OS: 'android' } }));
jest.mock('expo-auth-session', () => ({
  AuthRequest: jest.fn(() => {
    throw new Error('the browser flow must not run on Android');
  }),
  ResponseType: { Code: 'code' },
  exchangeCodeAsync: jest.fn(),
}));
jest.mock('react-native-credentials-manager', () => ({ signUpWithGoogle: jest.fn() }));

describe('Google sign-in on Android', () => {
  const previous = process.env.EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID;
  beforeEach(() => {
    process.env.EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID = 'web.apps.googleusercontent.com';
    jest.resetModules();
  });
  afterAll(() => {
    process.env.EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID = previous;
  });

  function load() {
    // Re-read the env the module inlines at load time.
    return jest.requireActual<typeof Google>('../src/auth/providers/google');
  }

  it('asks Credential Manager for an ID token with our nonce and the web client as audience', async () => {
    const { signUpWithGoogle: native } = jest.requireMock<{ signUpWithGoogle: jest.Mock }>(
      'react-native-credentials-manager',
    );
    native.mockResolvedValue({
      type: 'google-signin',
      id: 'ana@example.com',
      idToken: 'google-jwt',
      givenName: 'Ana',
      familyName: 'Kovač',
    });

    const credential = await load().signInWithGoogle('nonce-1');

    expect(native).toHaveBeenCalledWith({
      serverClientId: 'web.apps.googleusercontent.com',
      nonce: 'nonce-1',
      autoSelectEnabled: false,
      filterByAuthorizedAccounts: false,
    });
    expect(credential).toEqual({ idToken: 'google-jwt', firstName: 'Ana', lastName: 'Kovač' });
  });

  it('treats a dismissed account sheet as a cancel', async () => {
    const { signUpWithGoogle: native } = jest.requireMock<{ signUpWithGoogle: jest.Mock }>(
      'react-native-credentials-manager',
    );
    native.mockRejectedValue(new Error('activity is cancelled by the user.'));
    expect(await load().signInWithGoogle('nonce-1')).toBeNull();
  });

  it.each([
    'Developer console is not set up correctly.',
    '[28433] Cannot find a matching credential. The request was cancelled? No.',
    'No credentials available',
  ])('shows a real failure even if it mentions cancelling: %s', async (message) => {
    expect(load().isCancellation(new Error(message))).toBe(false);
  });

  it.each([
    'activity is cancelled by the user.',
    'During get sign-in intent, failure response from one tap: 16: Cancelled by user.',
    '[16] Cancelled by user',
  ])('treats %s as a cancel', (message) => {
    expect(load().isCancellation(new Error(message))).toBe(true);
  });

  it('passes other failures on', async () => {
    const { signUpWithGoogle: native } = jest.requireMock<{ signUpWithGoogle: jest.Mock }>(
      'react-native-credentials-manager',
    );
    native.mockRejectedValue(new Error('No credentials available'));
    await expect(load().signInWithGoogle('nonce-1')).rejects.toThrow('No credentials');
  });
});

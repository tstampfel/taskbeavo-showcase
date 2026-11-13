import { describe, expect, it } from 'vitest';
import {
  CODE_LENGTH,
  SignInError,
  accountDetailsInput,
  canCreateAccount,
  formatCountdown,
  isPlausibleEmail,
  isTermsNotAccepted,
  needsPhoneVerification,
  normalizeCode,
  signInErrorOf,
  stepAfterSignIn,
  type AccountDetailsForm,
} from './sign-in';

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
} as const;

describe('stepAfterSignIn', () => {
  it('signs an existing account in', () => {
    expect(stepAfterSignIn({ __typename: 'AuthSession', id: 's1', user })).toEqual({
      kind: 'signedIn',
      user,
    });
  });

  it('asks a new email address for its account details', () => {
    expect(
      stepAfterSignIn({
        __typename: 'AccountDetailsRequired',
        id: 't1',
        email: 'new@example.com',
        ticket: 'tkt',
      }),
    ).toEqual({
      kind: 'details',
      email: 'new@example.com',
      ticket: 'tkt',
      firstName: '',
      lastName: '',
      provider: undefined,
    });
  });

  it('pre-fills the name Google or Apple shared', () => {
    expect(
      stepAfterSignIn(
        {
          __typename: 'AccountDetailsRequired',
          id: 't1',
          email: 'x@privaterelay.example.com',
          ticket: 'tkt',
          firstName: 'Ana',
          lastName: null,
        },
        'APPLE',
      ),
    ).toMatchObject({ kind: 'details', firstName: 'Ana', lastName: '', provider: 'APPLE' });
  });

  it('never links a provider to an existing account without the emailed code', () => {
    expect(
      stepAfterSignIn(
        {
          __typename: 'ProviderLinkRequired',
          id: 'l1',
          email: 'ana@example.com',
          ticket: 'link',
        },
        'GOOGLE',
      ),
    ).toEqual({ kind: 'linkCode', email: 'ana@example.com', ticket: 'link', provider: 'GOOGLE' });
  });
});

describe('needsPhoneVerification', () => {
  it('sends unverified accounts to phone verification', () => {
    expect(needsPhoneVerification(user)).toBe(true);
    expect(needsPhoneVerification({ ...user, phoneVerified: true })).toBe(false);
  });
});

describe('codes', () => {
  it('keeps digits only, at most six', () => {
    expect(CODE_LENGTH).toBe(6);
    expect(normalizeCode(' 48 2-107 9')).toBe('482107');
    expect(normalizeCode('abc')).toBe('');
  });

  it('formats a countdown as m:ss', () => {
    expect(formatCountdown(42)).toBe('0:42');
    expect(formatCountdown(1752)).toBe('29:12');
    expect(formatCountdown(0)).toBe('0:00');
    expect(formatCountdown(-3)).toBe('0:00');
    expect(formatCountdown(4.2)).toBe('0:05');
  });
});

describe('isPlausibleEmail', () => {
  it('accepts an address and refuses obvious typos; the API has the last word', () => {
    expect(isPlausibleEmail(' ana.kovac@example.org ')).toBe(true);
    expect(isPlausibleEmail('ana.kovac')).toBe(false);
    expect(isPlausibleEmail('ana@')).toBe(false);
    expect(isPlausibleEmail('a b@example.org')).toBe(false);
  });
});

describe('account details', () => {
  const form: AccountDetailsForm = {
    firstName: ' Ana ',
    lastName: 'Kovač',
    country: 'DE',
    locale: 'de',
    isAdult: true,
    acceptedTerms: true,
    termsVersion: '2026-11',
  };

  it('needs both names, a country, the 18+ box and the terms box', () => {
    expect(canCreateAccount(form)).toBe(true);
    expect(canCreateAccount({ ...form, acceptedTerms: false })).toBe(false);
    expect(canCreateAccount({ ...form, isAdult: false })).toBe(false);
    expect(canCreateAccount({ ...form, firstName: '  ' })).toBe(false);
    expect(canCreateAccount({ ...form, lastName: '' })).toBe(false);
    expect(canCreateAccount({ ...form, country: '' })).toBe(false);
  });

  it('waits for the terms version in force', () => {
    expect(canCreateAccount({ ...form, termsVersion: undefined })).toBe(false);
  });

  it('builds the API input with the trimmed names and the device time zone', () => {
    expect(accountDetailsInput('tkt', form, 'Europe/Berlin')).toEqual({
      ticket: 'tkt',
      firstName: 'Ana',
      lastName: 'Kovač',
      isAdult: true,
      locale: 'DE',
      country: 'DE',
      timeZone: 'Europe/Berlin',
      acceptedTermsVersion: '2026-11',
    });
  });

  it('sends the terms version only when the box is ticked', () => {
    expect(
      accountDetailsInput('tkt', { ...form, acceptedTerms: false }, 'Europe/Berlin')
        .acceptedTermsVersion,
    ).toBeNull();
  });

  it('spots the error for terms that changed while the form was open', () => {
    expect(isTermsNotAccepted(new SignInError('TERMS_NOT_ACCEPTED'))).toBe(true);
    expect(isTermsNotAccepted(new SignInError('UNDERAGE'))).toBe(false);
  });
});

describe('signInErrorOf', () => {
  it('reads the code and retry time of an Apollo GraphQL error', () => {
    const error = {
      name: 'CombinedGraphQLErrors',
      errors: [{ message: 'x', extensions: { code: 'RATE_LIMITED', retryAfterSeconds: 1800 } }],
    };
    expect(signInErrorOf(error)).toEqual({ code: 'RATE_LIMITED', retryAfterSeconds: 1800 });
  });

  it('reads a SignInError thrown by the web client', () => {
    expect(signInErrorOf(new SignInError('INVALID_CODE'))).toEqual({
      code: 'INVALID_CODE',
      retryAfterSeconds: undefined,
    });
  });

  it('has no code for a network failure', () => {
    expect(signInErrorOf(new TypeError('Failed to fetch'))).toEqual({
      code: undefined,
      retryAfterSeconds: undefined,
    });
  });

  it('builds a SignInError from a response body', () => {
    const error = SignInError.fromErrors([
      { message: 'slow down', extensions: { code: 'RATE_LIMITED', retryAfterSeconds: 60 } },
    ]);
    expect(error.code).toBe('RATE_LIMITED');
    expect(error.retryAfterSeconds).toBe(60);
    expect(SignInError.fromErrors(undefined).code).toBeUndefined();
  });
});

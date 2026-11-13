import { SignInError } from '@taskbeavo/graphql';
import { catalogues } from '@taskbeavo/i18n';
import { createTranslator } from 'next-intl';
import { describe, expect, it } from 'vitest';
import { errorMessage } from './error-message';

// The app's translator, untyped like the one useTranslations() gives the sign-in components.
const t = createTranslator({ locale: 'en', messages: catalogues.en }) as unknown as Parameters<
  typeof errorMessage
>[0];

describe('errorMessage', () => {
  it('translates a known API code', () => {
    expect(errorMessage(t, new SignInError('INVALID_CODE'))).toBe(
      catalogues.en.errors.INVALID_CODE,
    );
  });

  it('says how long to wait when rate limited', () => {
    expect(errorMessage(t, new SignInError('RATE_LIMITED', 90))).toContain('1:30');
  });

  it('says how long to wait when sign-in is locked', () => {
    expect(errorMessage(t, new SignInError('LOCKED', 1800))).toContain('30:00');
  });

  it('never shows a raw key for a code the catalogues do not know', () => {
    expect(errorMessage(t, new SignInError('QUERY_TOO_COMPLEX'))).toBe(
      catalogues.en.signIn.networkError,
    );
  });

  it('treats a failure without a code as a network problem', () => {
    expect(errorMessage(t, new TypeError('fetch failed'))).toBe(catalogues.en.signIn.networkError);
  });
});

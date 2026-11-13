import { SignInError } from '@taskbeavo/graphql';
import { catalogues } from '@taskbeavo/i18n';
import { errorMessage } from '../src/components/sign-in/error-message';
import { i18n } from '../src/i18n';

describe('errorMessage', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('fr');
  });
  afterAll(async () => {
    await i18n.changeLanguage('hr');
  });

  it('translates a known API code', () => {
    expect(errorMessage(i18n.t, new SignInError('INVALID_CODE'))).toBe(
      catalogues.fr.errors.INVALID_CODE,
    );
  });

  it('says how long to wait when sign-in is locked', () => {
    expect(errorMessage(i18n.t, new SignInError('LOCKED', 1800))).toContain('30:00');
  });

  it('never shows a raw key for a code the catalogues do not know', () => {
    expect(errorMessage(i18n.t, new SignInError('QUERY_TOO_COMPLEX'))).toBe(
      catalogues.fr.signIn.networkError,
    );
  });
});

import { SignInError, type SignInAnswer, type ViewerFieldsFragment } from '@taskbeavo/graphql';
import { catalogues } from '@taskbeavo/i18n';
import { colors, minTapTarget, radii } from '@taskbeavo/ui-tokens';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import { StyleSheet } from 'react-native';
import { i18n } from '../src/i18n';
import type { LanguageDeps } from '../src/i18n/preference';
import { SignInScreen, type SignInScreenProps } from '../src/screens/sign-in-screen';

const en = catalogues.en;

const user: ViewerFieldsFragment = {
  id: 'u1',
  email: 'ana@example.com',
  firstName: 'Ana',
  lastName: 'Kovač',
  locale: 'EN',
  country: 'DE',
  timeZone: 'Europe/Berlin',
  phoneVerified: false,
  phoneNumberMasked: null,
};
const session: SignInAnswer = { __typename: 'AuthSession', id: 's1', user };
const sessionWith = (locale: ViewerFieldsFragment['locale']): SignInAnswer => ({
  ...session,
  user: { ...user, locale },
});

function language(picked: string | null = null) {
  let pick = picked;
  return {
    i18n: { changeLanguage: jest.fn(async (locale: string) => i18n.changeLanguage(locale)) },
    store: { load: () => null, save: jest.fn() },
    picked: { load: () => pick, save: jest.fn((locale: string | null) => void (pick = locale)) },
    saveToAccount: jest.fn(async () => undefined),
  } satisfies LanguageDeps;
}

function api(overrides: Partial<SignInScreenProps['api']> = {}): SignInScreenProps['api'] {
  return {
    requestEmailCode: jest.fn(async () => undefined),
    verifyEmailCode: jest.fn(async () => session),
    requestNonce: jest.fn(async () => 'nonce-1'),
    signInWithProvider: jest.fn(async () => session),
    linkProvider: jest.fn(async () => session),
    completeAccountDetails: jest.fn(async () => session),
    currentTermsVersion: jest.fn(async () => '2026-11'),
    ...overrides,
  };
}

async function show(props: Partial<SignInScreenProps> = {}) {
  const onSignedIn = jest.fn();
  const all: SignInScreenProps = {
    api: api(),
    providers: {},
    language: language(),
    timeZone: 'Europe/Berlin',
    deviceLanguageTags: ['de-DE'],
    openLegal: jest.fn(),
    onSignedIn,
    ...props,
  };
  await render(<SignInScreen {...all} />);
  return { ...all, onSignedIn };
}

async function emailCode(code = '482107') {
  await fireEvent.press(screen.getByTestId('email-sign-in'));
  await fireEvent.changeText(screen.getByTestId('email'), 'ana@example.com');
  await fireEvent.press(screen.getByTestId('send-code'));
  await screen.findByTestId('email-code');
  await fireEvent.changeText(screen.getByTestId('email-code-input'), code);
}

const detailsRequired = jest.fn(async () => ({
  __typename: 'AccountDetailsRequired' as const,
  id: 'adr1',
  ticket: 't1',
  email: 'ana@example.com',
  firstName: 'Ana',
  lastName: 'Kovač',
}));

/** From the email code to a details form with both boxes ticked. */
async function fillDetails() {
  await emailCode();
  await screen.findByTestId('account-details');
  await fireEvent.press(screen.getByTestId('is-adult'));
  await fireEvent.press(screen.getByTestId('accept-terms'));
}

beforeEach(async () => {
  await i18n.changeLanguage('en');
});
afterAll(async () => {
  await i18n.changeLanguage('hr');
});

describe('SignInScreen', () => {
  it('signs in an existing account with the email code', async () => {
    const props = await show();
    await emailCode();

    await waitFor(() => expect(props.onSignedIn).toHaveBeenCalledWith(user));
    expect(props.api.requestEmailCode).toHaveBeenCalledWith('ana@example.com', 'EN');
    expect(props.api.verifyEmailCode).toHaveBeenCalledWith('ana@example.com', '482107');
  });

  it('refuses an implausible email before calling the API', async () => {
    const props = await show();
    await fireEvent.press(screen.getByTestId('email-sign-in'));
    await fireEvent.changeText(screen.getByTestId('email'), 'ana@');
    await fireEvent.press(screen.getByTestId('send-code'));

    expect(await screen.findByText(en.signIn.emailInvalid)).toBeOnTheScreen();
    expect(props.api.requestEmailCode).not.toHaveBeenCalled();
  });

  it('says a wrong code is wrong, and shows the lockout with its timer', async () => {
    const verifyEmailCode = jest
      .fn()
      .mockRejectedValueOnce(new SignInError('INVALID_CODE'))
      .mockRejectedValueOnce(new SignInError('RATE_LIMITED', 1800));
    await show({ api: api({ verifyEmailCode }) });
    await emailCode('000000');

    expect(await screen.findByText(en.errors.INVALID_CODE)).toBeOnTheScreen();
    await fireEvent.changeText(screen.getByTestId('email-code-input'), '111111');
    expect(await screen.findByTestId('email-code-locked')).toHaveTextContent(/30:00/);
  });

  it('creates a new account only once 18+ and the terms are ticked', async () => {
    const completeAccountDetails = jest.fn(async () => session);
    const props = await show({
      api: api({
        verifyEmailCode: jest.fn(async () => ({
          __typename: 'AccountDetailsRequired' as const,
          id: 'adr1',
          ticket: 't1',
          email: 'ana@example.com',
          firstName: '',
          lastName: '',
        })),
        completeAccountDetails,
      }),
    });
    await emailCode();
    await screen.findByTestId('account-details');

    await fireEvent.changeText(screen.getByTestId('first-name'), 'Ana');
    await fireEvent.changeText(screen.getByTestId('last-name'), 'Kovač');
    // The phone's region picks the country; both boxes start unticked.
    expect(screen.getByTestId('country')).toHaveAccessibleName(/Germany/);
    expect(screen.getByTestId('accept-terms')).not.toBeChecked();
    expect(screen.getByTestId('create-account')).toBeDisabled();
    await fireEvent.press(screen.getByTestId('is-adult'));
    expect(screen.getByTestId('create-account')).toBeDisabled();
    await fireEvent.press(screen.getByTestId('accept-terms'));
    await fireEvent.press(screen.getByTestId('legal-terms'));
    expect(props.openLegal).toHaveBeenCalledWith('terms', 'en');
    await fireEvent.press(screen.getByTestId('account-language-de'));
    // A new language unticks the box: the terms are ticked again for the German text.
    expect(screen.getByTestId('accept-terms')).not.toBeChecked();
    await fireEvent.press(screen.getByTestId('accept-terms'));
    // The terms open in the account's language, the one the acceptance records.
    await fireEvent.press(screen.getByTestId('legal-terms'));
    expect(props.openLegal).toHaveBeenLastCalledWith('terms', 'de');
    await fireEvent.press(screen.getByTestId('create-account'));

    expect(completeAccountDetails).toHaveBeenCalledWith({
      ticket: 't1',
      firstName: 'Ana',
      lastName: 'Kovač',
      isAdult: true,
      locale: 'DE',
      country: 'DE',
      timeZone: 'Europe/Berlin',
      acceptedTermsVersion: '2026-11',
    });
    expect(props.api.currentTermsVersion).toHaveBeenCalledWith('DE');
    await waitFor(() => expect(props.onSignedIn).toHaveBeenCalled());
  });

  it('unticks the terms and loads the new version when the API refuses the old one', async () => {
    const completeAccountDetails = jest
      .fn()
      .mockRejectedValueOnce(new SignInError('TERMS_NOT_ACCEPTED'))
      .mockResolvedValueOnce(session);
    const currentTermsVersion = jest
      .fn()
      .mockResolvedValueOnce('2026-11')
      .mockResolvedValueOnce('2027-01');
    const props = await show({
      api: api({ verifyEmailCode: detailsRequired, completeAccountDetails, currentTermsVersion }),
    });
    await fillDetails();
    await fireEvent.press(screen.getByTestId('create-account'));

    expect(await screen.findByText(en.errors.TERMS_NOT_ACCEPTED)).toBeOnTheScreen();
    expect(screen.getByTestId('accept-terms')).not.toBeChecked();
    expect(screen.getByTestId('create-account')).toBeDisabled();
    await waitFor(() => expect(currentTermsVersion).toHaveBeenCalledTimes(2));

    await fireEvent.press(screen.getByTestId('accept-terms'));
    await fireEvent.press(screen.getByTestId('create-account'));
    expect(completeAccountDetails).toHaveBeenLastCalledWith(
      expect.objectContaining({ acceptedTermsVersion: '2027-01' }),
    );
    await waitFor(() => expect(props.onSignedIn).toHaveBeenCalled());
  });

  it('keeps "Create account" disabled while no terms version can be loaded, until a retry', async () => {
    const currentTermsVersion = jest
      .fn()
      .mockResolvedValueOnce(null)
      .mockRejectedValueOnce(new TypeError('offline'))
      .mockResolvedValueOnce('2026-11');
    const props = await show({
      api: api({ verifyEmailCode: detailsRequired, currentTermsVersion }),
    });
    await fillDetails();

    expect(await screen.findByText(en.signIn.termsUnavailable)).toBeOnTheScreen();
    expect(screen.getByTestId('create-account')).toBeDisabled();
    await fireEvent.press(screen.getByTestId('terms-retry'));
    // Offline on the retry: still unavailable, and the retry stays.
    expect(await screen.findByTestId('terms-retry')).toBeOnTheScreen();
    await fireEvent.press(screen.getByTestId('terms-retry'));

    await waitFor(() => expect(screen.queryByTestId('terms-retry')).toBeNull());
    expect(currentTermsVersion).toHaveBeenCalledTimes(3);
    await fireEvent.press(screen.getByTestId('create-account'));
    expect(props.api.completeAccountDetails).toHaveBeenCalledWith(
      expect.objectContaining({ acceptedTermsVersion: '2026-11' }),
    );
  });

  it('unticks the terms box when the account language changes', async () => {
    await show({ api: api({ verifyEmailCode: detailsRequired }) });
    await fillDetails();
    await waitFor(() => expect(screen.getByTestId('create-account')).toBeEnabled());

    await fireEvent.press(screen.getByTestId('account-language-de'));

    expect(screen.getByTestId('accept-terms')).not.toBeChecked();
    expect(screen.getByTestId('create-account')).toBeDisabled();
  });

  it('links Google to an existing account only after the emailed code', async () => {
    const google = jest.fn(async () => ({ idToken: 'google-jwt' }));
    const linkProvider = jest.fn(async () => session);
    const props = await show({
      providers: { google },
      api: api({
        signInWithProvider: jest.fn(async () => ({
          __typename: 'ProviderLinkRequired' as const,
          id: 'plr1',
          ticket: 'link-1',
          email: 'ana@example.com',
        })),
        linkProvider,
      }),
    });

    await fireEvent.press(screen.getByTestId('google-sign-in'));
    expect(google).toHaveBeenCalledWith('nonce-1');
    expect(await screen.findByTestId('link-code')).toBeOnTheScreen();
    expect(props.onSignedIn).not.toHaveBeenCalled();
    await fireEvent.changeText(screen.getByTestId('link-code-input'), '123456');

    expect(linkProvider).toHaveBeenCalledWith('link-1', '123456');
    await waitFor(() => expect(props.onSignedIn).toHaveBeenCalledWith(user));
  });

  it('stays on the welcome screen when the provider sheet is cancelled', async () => {
    const props = await show({ providers: { apple: jest.fn(async () => null) } });
    await fireEvent.press(screen.getByTestId('apple-sign-in'));
    expect(props.api.signInWithProvider).not.toHaveBeenCalled();
    expect(screen.getByTestId('sign-in-welcome')).toBeOnTheScreen();
  });

  it('adopts the account language when no language was picked on the phone', async () => {
    const lang = language();
    const props = await show({
      language: lang,
      api: api({
        verifyEmailCode: jest.fn(async () => sessionWith('DE')),
      }),
    });
    await emailCode();

    await waitFor(() => expect(props.onSignedIn).toHaveBeenCalled());
    expect(lang.i18n.changeLanguage).toHaveBeenCalledWith('de');
    expect(lang.saveToAccount).not.toHaveBeenCalled();
  });

  it.each([
    ['locale-yes', [['FR']]],
    ['locale-no', []],
  ])('asks before saving a different pick on the account (%s)', async (answer, saved) => {
    const lang = language();
    const props = await show({
      language: lang,
      api: api({
        verifyEmailCode: jest.fn(async () => sessionWith('HR')),
      }),
    });
    await fireEvent.press(screen.getByTestId('language-fr'));
    expect(lang.picked.save).toHaveBeenCalledWith('fr');
    // Signed out, the real saveToAccount does nothing; only the answer counts here.
    lang.saveToAccount.mockClear();
    await emailCode();

    expect(await screen.findByTestId('locale-prompt')).toHaveTextContent(/Français/);
    expect(props.onSignedIn).not.toHaveBeenCalled();
    expect(i18n.language).toBe('fr');
    await fireEvent.press(screen.getByTestId(answer));

    await waitFor(() => expect(props.onSignedIn).toHaveBeenCalled());
    expect(lang.saveToAccount.mock.calls).toEqual(saved);
    expect(i18n.language).toBe('fr');
  });
});

// BEAV-215: the sign-in steps use the app's shared top bar and controls.
describe('SignInScreen design', () => {
  it('has the shared top bar: a round, labelled back button and the title as heading', async () => {
    await show();
    await fireEvent.press(screen.getByTestId('email-sign-in'));
    const back = screen.getByTestId('screen-back');
    expect(back.props.accessibilityLabel).toBe(en.signIn.back);
    expect(StyleSheet.flatten(back.props.style)).toMatchObject({
      width: minTapTarget,
      height: minTapTarget,
      borderRadius: radii.pill,
      backgroundColor: colors.white,
    });
    expect(screen.getByRole('header', { name: en.signIn.continueWithEmail })).toBeTruthy();
    // Drawn, not a "‹" glyph.
    expect(screen.queryByText('‹', { includeHiddenElements: true })).toBeNull();
    await fireEvent.press(back);
    expect(screen.getByTestId('sign-in-welcome')).toBeTruthy();
  });

  it('draws the welcome screen without a top bar', async () => {
    await show();
    expect(screen.queryByTestId('screen-back')).toBeNull();
    expect(screen.getByRole('header', { name: en.signIn.welcomeTitle })).toBeTruthy();
  });

  it('outlines an unticked box in the mockups’ grey, filled green once ticked', async () => {
    await show({ api: api({ verifyEmailCode: detailsRequired }) });
    await emailCode();
    await screen.findByTestId('account-details');
    const box = () => StyleSheet.flatten(screen.getByTestId('is-adult-box').props.style);
    expect(box()).toMatchObject({ borderColor: colors.inkMuted, backgroundColor: colors.white });
    await fireEvent.press(screen.getByTestId('is-adult'));
    expect(box()).toMatchObject({ borderColor: colors.green, backgroundColor: colors.green });
  });

  it('shows the lockout in a white card', async () => {
    await show({
      api: api({
        verifyEmailCode: jest.fn(async () => {
          throw new SignInError('LOCKED', 600);
        }),
      }),
    });
    await emailCode();
    const locked = await screen.findByTestId('email-code-locked');
    expect(StyleSheet.flatten(locked.props.style)).toMatchObject({
      backgroundColor: colors.white,
      borderRadius: radii.lg,
    });
    expect(screen.getByRole('header', { name: en.signIn.lockedTitle })).toBeTruthy();
  });
});

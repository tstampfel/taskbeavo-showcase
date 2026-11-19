import { MockedProvider } from '@apollo/client/testing/react';
import { ApiVersionDocument, FeedHelperProfileDocument, ViewerDocument } from '@taskbeavo/graphql';
import { catalogues, locales } from '@taskbeavo/i18n';
import { render, screen } from '@testing-library/react-native';
import Welcome from '../app/index';
import { i18n } from '../src/i18n';

const apiVersion = {
  request: { query: ApiVersionDocument },
  result: { data: { apiVersion: '0.1.0' } },
};
// Signed out: the account row offers "Log in".
const signedOut = { request: { query: ViewerDocument }, result: { data: { viewer: null } } };

describe('Welcome screen', () => {
  afterAll(async () => {
    await i18n.changeLanguage('hr');
  });

  it.each(locales)('renders the %s catalogue', async (locale) => {
    await i18n.changeLanguage(locale);
    const messages = catalogues[locale];
    await render(
      <MockedProvider mocks={[apiVersion, signedOut]}>
        <Welcome />
      </MockedProvider>,
    );

    expect(screen.getByRole('header')).toHaveTextContent(messages.welcome.headline);
    expect(screen.getByText(messages.welcome.body)).toBeOnTheScreen();
    expect(screen.getByText(messages.home.comingSoon)).toBeOnTheScreen();
    expect(await screen.findByTestId('log-in')).toHaveTextContent(messages.signIn.signIn);
  });

  it('keeps text free to wrap at large font sizes', async () => {
    await i18n.changeLanguage('de');
    await render(
      <MockedProvider mocks={[apiVersion, signedOut]}>
        <Welcome />
      </MockedProvider>,
    );
    const headline = screen.getByRole('header');
    expect(headline.props.numberOfLines).toBeUndefined();
    expect(headline.props.allowFontScaling).not.toBe(false);
  });
});

describe('Home when signed in', () => {
  it('shows the task feed with the terms banner, account row and language switcher', async () => {
    await i18n.changeLanguage('en');
    const viewer = {
      __typename: 'User',
      id: 'u1',
      email: 'luka@example.com',
      firstName: 'Luka',
      lastName: 'Test',
      locale: 'EN',
      country: 'HR',
      timeZone: 'Europe/Zagreb',
      phoneVerified: true,
      phoneNumberMasked: '+385 ••• 678',
    };
    await render(
      <MockedProvider
        mocks={[
          { request: { query: ViewerDocument }, result: { data: { viewer } }, maxUsageCount: 5 },
          {
            request: { query: FeedHelperProfileDocument },
            result: { data: { myHelperProfile: null } },
          },
          apiVersion,
        ]}
      >
        <Welcome />
      </MockedProvider>,
    );
    expect(await screen.findByTestId('task-feed')).toBeOnTheScreen();
    expect(screen.getByRole('header')).toHaveTextContent(/Luka/);
    // Signing out and switching language stay on home until the account tab exists.
    expect(await screen.findByTestId('sign-out')).toBeOnTheScreen();
    expect(screen.getByTestId('language-switcher')).toBeOnTheScreen();
  });
});

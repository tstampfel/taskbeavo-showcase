import { MockedProvider } from '@apollo/client/testing/react';
import { ApiVersionDocument, ViewerDocument } from '@taskbeavo/graphql';
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

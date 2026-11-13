import { MockedProvider } from '@apollo/client/testing/react';
import { ApiVersionDocument } from '@taskbeavo/graphql';
import { catalogues, locales } from '@taskbeavo/i18n';
import { render, screen } from '@testing-library/react-native';
import { ApiStatus } from '../src/components/api-status';
import { i18n } from '../src/i18n';

const ok = { request: { query: ApiVersionDocument }, result: { data: { apiVersion: '9.9.9' } } };
const down = { request: { query: ApiVersionDocument }, error: new Error('Network request failed') };

describe('ApiStatus', () => {
  afterAll(async () => {
    await i18n.changeLanguage('hr');
  });

  it.each(locales)('renders the ApiVersion query result in %s', async (locale) => {
    await i18n.changeLanguage(locale);
    await render(
      <MockedProvider mocks={[ok]}>
        <ApiStatus />
      </MockedProvider>,
    );

    expect(screen.getByTestId('api-status')).toHaveTextContent(
      catalogues[locale].apiStatus.connecting,
    );
    expect(
      await screen.findByText(catalogues[locale].apiStatus.connected.replace('{version}', '9.9.9')),
    ).toBeOnTheScreen();
  });

  it('shows a translated message when the API is unreachable', async () => {
    await i18n.changeLanguage('de');
    await render(
      <MockedProvider mocks={[down]}>
        <ApiStatus />
      </MockedProvider>,
    );

    expect(await screen.findByText(catalogues.de.apiStatus.unavailable)).toBeOnTheScreen();
  });
});

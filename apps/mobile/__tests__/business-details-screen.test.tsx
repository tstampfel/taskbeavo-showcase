import { MockedProvider } from '@apollo/client/testing/react';
import {
  ClearPosterBusinessDocument,
  PosterBusinessDocument,
  SetPosterBusinessDocument,
} from '@taskbeavo/graphql';
import { catalogues } from '@taskbeavo/i18n';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import { GraphQLError } from 'graphql';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { i18n } from '../src/i18n';
import { BusinessDetailsScreen } from '../src/screens/business-details-screen';

const en = catalogues.en.businessDetails;
// Made-up digits, not a real OIB: the API is mocked, so the check digit doesn't matter.
const OIB = '12345678901';
const MASKED = '•••••••8901';
const metrics = {
  frame: { x: 0, y: 0, width: 390, height: 844 },
  insets: { top: 47, left: 0, right: 0, bottom: 34 },
};

const business = (extra: Record<string, unknown> = {}) => ({
  __typename: 'PosterBusiness',
  id: 'pb-1',
  kind: 'OBRT',
  name: 'Obrt Primjer',
  oibMasked: MASKED,
  ...extra,
});
const user = (posterBusiness: unknown) => ({ __typename: 'User', id: 'u1', posterBusiness });
const loaded = (posterBusiness: unknown) => ({
  request: { query: PosterBusinessDocument },
  result: { data: { viewer: user(posterBusiness) } },
});
const setMock = (input: Record<string, unknown>, result: Record<string, unknown>) => ({
  request: { query: SetPosterBusinessDocument, variables: { input } },
  result,
});
const failure = (code: string) => ({
  errors: [new GraphQLError('refused', { extensions: { code } })],
});

async function renderScreen(mocks: unknown[]) {
  const onSignedOut = jest.fn();
  await render(
    <SafeAreaProvider initialMetrics={metrics}>
      <MockedProvider mocks={mocks as never}>
        <BusinessDetailsScreen onBack={jest.fn()} onSignedOut={onSignedOut} />
      </MockedProvider>
    </SafeAreaProvider>,
  );
  return { onSignedOut };
}

async function fillAndConfirm({ company = false } = {}) {
  await fireEvent.press(await screen.findByTestId('business-add'));
  if (company) await fireEvent.press(screen.getByTestId('business-kind-COMPANY'));
  await fireEvent.changeText(screen.getByTestId('business-name'), '  Obrt Primjer ');
  await fireEvent.changeText(screen.getByTestId('business-oib-input'), OIB);
  await fireEvent.press(screen.getByTestId('business-save'));
  expect(await screen.findByText(en.confirmBody)).toBeOnTheScreen();
  await fireEvent.press(screen.getByTestId('business-confirm'));
}

beforeAll(async () => {
  await i18n.changeLanguage('en');
});
afterAll(async () => {
  await i18n.changeLanguage('hr');
});

describe('BusinessDetailsScreen', () => {
  it('adds a business after explaining what changes, and shows only the masked OIB', async () => {
    await renderScreen([
      loaded(null),
      setMock(
        { kind: 'OBRT', name: 'Obrt Primjer', oib: OIB },
        { data: { setPosterBusiness: user(business()) } },
      ),
    ]);

    expect(await screen.findByText(en.privatePoster)).toBeOnTheScreen();
    await fillAndConfirm();

    expect(await screen.findByText(en.saved)).toBeOnTheScreen();
    expect(screen.getByTestId('business-oib')).toHaveTextContent(`OIB ${MASKED}`);
    expect(screen.queryByText(new RegExp(OIB))).toBeNull();
  });

  it('asks for confirmation and saves nothing when the poster cancels', async () => {
    // No SetPosterBusiness mock: a call would fail the save and show an error.
    await renderScreen([loaded(null)]);
    await fireEvent.press(await screen.findByTestId('business-add'));
    await fireEvent.changeText(screen.getByTestId('business-name'), 'Obrt Primjer');
    await fireEvent.changeText(screen.getByTestId('business-oib-input'), OIB);
    await fireEvent.press(screen.getByTestId('business-save'));

    expect(await screen.findByText(en.confirmTitle)).toBeOnTheScreen();
    await fireEvent.press(screen.getByTestId('business-confirm-cancel'));
    await waitFor(() => expect(screen.queryByText(en.confirmBody)).toBeNull());
    expect(screen.getByTestId('business-form')).toBeOnTheScreen();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('edits the saved business: name and kind prefilled, the OIB asked for again', async () => {
    await renderScreen([
      loaded(business()),
      setMock(
        { kind: 'COMPANY', name: 'Primjer d.o.o.', oib: OIB },
        {
          data: {
            setPosterBusiness: user(business({ kind: 'COMPANY', name: 'Primjer d.o.o.' })),
          },
        },
      ),
    ]);

    expect(await screen.findByTestId('business-oib')).toHaveTextContent(`OIB ${MASKED}`);
    await fireEvent.press(screen.getByTestId('business-edit'));
    expect(screen.getByTestId('business-name')).toHaveDisplayValue('Obrt Primjer');
    expect(screen.getByTestId('business-oib-input')).toHaveDisplayValue('');
    expect(screen.getByText(en.oibReenter)).toBeOnTheScreen();

    await fireEvent.press(screen.getByTestId('business-kind-COMPANY'));
    expect(screen.getByText(en.oibHintCompany)).toBeOnTheScreen();
    await fireEvent.changeText(screen.getByTestId('business-name'), 'Primjer d.o.o.');
    await fireEvent.changeText(screen.getByTestId('business-oib-input'), OIB);
    await fireEvent.press(screen.getByTestId('business-save'));
    await fireEvent.press(await screen.findByTestId('business-confirm'));

    expect(await screen.findByText(en.saved)).toBeOnTheScreen();
    expect(screen.getByText('Primjer d.o.o.')).toBeOnTheScreen();
    expect(screen.getByText(en.kind.COMPANY)).toBeOnTheScreen();
  });

  it('removes the business after confirmation', async () => {
    await renderScreen([
      loaded(business()),
      {
        request: { query: ClearPosterBusinessDocument },
        result: { data: { clearPosterBusiness: user(null) } },
      },
    ]);

    await fireEvent.press(await screen.findByTestId('business-remove'));
    expect(await screen.findByText(en.removeBody)).toBeOnTheScreen();
    await fireEvent.press(screen.getByTestId('business-remove-confirm'));

    expect(await screen.findByText(en.removed)).toBeOnTheScreen();
    expect(screen.getByText(en.privatePoster)).toBeOnTheScreen();
    expect(screen.queryByTestId('business-saved')).toBeNull();
  });

  it('says so and keeps the business when removing fails', async () => {
    await renderScreen([
      loaded(business()),
      {
        request: { query: ClearPosterBusinessDocument },
        error: new TypeError('Network request failed'),
      },
    ]);

    await fireEvent.press(await screen.findByTestId('business-remove'));
    await fireEvent.press(await screen.findByTestId('business-remove-confirm'));

    expect(await screen.findByText(catalogues.en.signIn.networkError)).toBeOnTheScreen();
    expect(screen.getByTestId('business-saved')).toBeOnTheScreen();
    expect(screen.queryByText(en.removed)).toBeNull();
  });

  it('clears the OIB error when the kind changes', async () => {
    await renderScreen([
      loaded(null),
      setMock({ kind: 'OBRT', name: 'Obrt Primjer', oib: OIB }, failure('INVALID_BUSINESS_OIB')),
    ]);
    await fillAndConfirm();
    expect(await screen.findByText(en.invalidObrtOib)).toBeOnTheScreen();

    await fireEvent.press(screen.getByTestId('business-kind-COMPANY'));
    expect(screen.queryByText(en.invalidObrtOib)).toBeNull();
  });

  it.each([
    ['INVALID_OIB', en.invalidOib],
    ['INVALID_BUSINESS_OIB', en.invalidObrtOib],
    ['POSTER_BUSINESS_CHECKOUT_OPEN', catalogues.en.errors.POSTER_BUSINESS_CHECKOUT_OPEN],
    ['INVALID_POSTER_BUSINESS', catalogues.en.errors.INVALID_POSTER_BUSINESS],
    ['RATE_LIMITED', catalogues.en.errors.RATE_LIMITED],
  ])('shows %s as a friendly message and keeps the form', async (code, message) => {
    await renderScreen([
      loaded(null),
      setMock({ kind: 'COMPANY', name: 'Obrt Primjer', oib: OIB }, failure(code)),
    ]);
    await fillAndConfirm({ company: true });

    expect(await screen.findByText(message)).toBeOnTheScreen();
    expect(screen.getByTestId('business-form')).toBeOnTheScreen();
    expect(screen.getByTestId('business-oib-input')).toHaveDisplayValue(OIB);
  });

  it('shows an error with retry when the details cannot be loaded', async () => {
    await renderScreen([
      {
        request: { query: PosterBusinessDocument },
        error: new TypeError('Network request failed'),
      },
      loaded(null),
    ]);

    expect(await screen.findByText(catalogues.en.signIn.networkError)).toBeOnTheScreen();
    await fireEvent.press(screen.getByTestId('business-retry'));
    expect(await screen.findByText(en.privatePoster)).toBeOnTheScreen();
  });

  it('sends a signed-out user to sign-in', async () => {
    const { onSignedOut } = await renderScreen([
      { request: { query: PosterBusinessDocument }, result: { data: { viewer: null } } },
    ]);
    await waitFor(() => expect(onSignedOut).toHaveBeenCalledTimes(1));
  });
});

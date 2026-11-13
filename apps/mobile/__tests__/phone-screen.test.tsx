import { MockedProvider } from '@apollo/client/testing/react';
import { GraphQLError } from 'graphql';
import {
  RequestPhoneCodeDocument,
  VerifyPhoneCodeDocument,
  ViewerDocument,
} from '@taskbeavo/graphql';
import { catalogues } from '@taskbeavo/i18n';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import { i18n } from '../src/i18n';
import { PhoneScreen } from '../src/screens/phone-screen';

const viewer = {
  __typename: 'User',
  id: 'u1',
  email: 'ana@example.com',
  firstName: 'Ana',
  lastName: 'Kovač',
  locale: 'DE',
  country: 'DE',
  timeZone: 'Europe/Berlin',
  phoneVerified: false,
  phoneNumberMasked: null,
};
const viewerMock = { request: { query: ViewerDocument }, result: { data: { viewer } } };
const sent = {
  __typename: 'PhoneCodeRequest',
  id: 'p1',
  phoneNumberMasked: '+49 ••• 678',
  expiresAt: '2030-01-01T00:10:00Z',
  resendAvailableAt: '2030-01-01T00:01:00Z',
};

beforeAll(async () => {
  await i18n.changeLanguage('de');
});
afterAll(async () => {
  await i18n.changeLanguage('hr');
});

describe('PhoneScreen', () => {
  it('shows an error with retry when the account cannot be loaded', async () => {
    await render(
      <MockedProvider
        mocks={[
          { request: { query: ViewerDocument }, error: new TypeError('Network request failed') },
          viewerMock,
        ]}
      >
        <PhoneScreen onDone={jest.fn()} onSignedOut={jest.fn()} />
      </MockedProvider>,
    );

    expect(await screen.findByText(catalogues.de.signIn.networkError)).toBeOnTheScreen();
    await fireEvent.press(screen.getByTestId('phone-retry'));
    expect(await screen.findByTestId('phone-input')).toHaveDisplayValue('+49 ');
  });

  it('sends a signed-out user to sign-in once, after rendering', async () => {
    const onSignedOut = jest.fn();
    await render(
      <MockedProvider
        mocks={[{ request: { query: ViewerDocument }, result: { data: { viewer: null } } }]}
      >
        <PhoneScreen onDone={jest.fn()} onSignedOut={onSignedOut} />
      </MockedProvider>,
    );
    await waitFor(() => expect(onSignedOut).toHaveBeenCalledTimes(1));
  });

  it('says plainly that +1 numbers cannot be texted yet', async () => {
    await render(
      <MockedProvider
        mocks={[
          viewerMock,
          {
            request: { query: RequestPhoneCodeDocument, variables: { phone: '+1 415 555 0100' } },
            result: {
              errors: [
                new GraphQLError('no', { extensions: { code: 'PHONE_COUNTRY_NOT_SUPPORTED' } }),
              ],
            },
          },
        ]}
      >
        <PhoneScreen onDone={jest.fn()} onSignedOut={jest.fn()} />
      </MockedProvider>,
    );

    // Prefilled with the account country's calling code.
    expect(await screen.findByTestId('phone-input')).toHaveDisplayValue('+49 ');
    await fireEvent.changeText(screen.getByTestId('phone-input'), '+1 415 555 0100');
    await fireEvent.press(screen.getByTestId('send-sms'));

    expect(
      await screen.findByText(catalogues.de.errors.PHONE_COUNTRY_NOT_SUPPORTED),
    ).toBeOnTheScreen();
  });

  it('verifies the SMS code and moves on', async () => {
    const onDone = jest.fn();
    await render(
      <MockedProvider
        mocks={[
          viewerMock,
          {
            request: { query: RequestPhoneCodeDocument, variables: { phone: '+49 151 2345678' } },
            result: { data: { requestPhoneCode: sent } },
          },
          {
            request: { query: VerifyPhoneCodeDocument, variables: { code: '246810' } },
            result: {
              data: {
                verifyPhoneCode: {
                  ...viewer,
                  phoneVerified: true,
                  phoneNumberMasked: '+49 ••• 678',
                },
              },
            },
          },
        ]}
      >
        <PhoneScreen onDone={onDone} onSignedOut={jest.fn()} />
      </MockedProvider>,
    );

    await fireEvent.changeText(await screen.findByTestId('phone-input'), '+49 151 2345678');
    await fireEvent.press(screen.getByTestId('send-sms'));
    expect(await screen.findByText(/\+49 ••• 678/)).toBeOnTheScreen();
    await fireEvent.changeText(screen.getByTestId('phone-code-input'), '246810');

    await waitFor(() => expect(onDone).toHaveBeenCalled());
  });

  it('sends one new SMS for a double tap on resend', async () => {
    const sentNow = { ...sent, resendAvailableAt: '2000-01-01T00:00:00Z' };
    const request = { query: RequestPhoneCodeDocument, variables: { phone: '+49 151 2345678' } };
    const resent = jest.fn(() => ({ data: { requestPhoneCode: sentNow } }));
    await render(
      <MockedProvider
        mocks={[
          viewerMock,
          { request, result: { data: { requestPhoneCode: sentNow } } },
          // Only one resend is answered; a second request would fail and show an error.
          { request, delay: 100, result: resent },
        ]}
      >
        <PhoneScreen onDone={jest.fn()} onSignedOut={jest.fn()} />
      </MockedProvider>,
    );
    await fireEvent.changeText(await screen.findByTestId('phone-input'), '+49 151 2345678');
    await fireEvent.press(screen.getByTestId('send-sms'));
    const resend = await screen.findByTestId('phone-code-resend');

    await fireEvent.press(resend);
    await fireEvent.press(resend);

    expect(await screen.findByText(catalogues.de.signIn.codeResent)).toBeOnTheScreen();
    expect(resent).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId('phone-code-input-error')).toBeNull();
  });
});

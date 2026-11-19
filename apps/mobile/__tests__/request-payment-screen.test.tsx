import { ApolloClient, ApolloLink, InMemoryCache } from '@apollo/client';
import { ApolloProvider } from '@apollo/client/react';
import { typePolicies } from '@taskbeavo/graphql';
import { catalogues, formatMoney, locales } from '@taskbeavo/i18n';
import { fireEvent, render, screen } from '@testing-library/react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { Observable } from 'rxjs';
import { i18n } from '../src/i18n';
import { RequestPaymentScreen } from '../src/screens/request-payment-screen';

const metrics = {
  frame: { x: 0, y: 0, width: 390, height: 844 },
  insets: { top: 47, left: 0, right: 0, bottom: 34 },
};
const en = catalogues.en.requestPayment;
const JIR = '6b8a3c2e-1f4d-4a7b-9c0e-5d2f8a1b3c4d';

function booking(extra: Record<string, unknown> = {}) {
  return {
    __typename: 'Booking',
    id: 'booking-1',
    taskId: 'task-1',
    offerId: 'offer-1',
    helperUserId: 'user-1',
    helperIsTrader: false,
    status: 'FUNDS_HELD',
    amountCents: 3_000,
    feeCents: 360,
    creditCents: null,
    totalCents: 3_360,
    slotStart: '2026-11-08T09:00:00Z',
    termsVersion: 'draft-1',
    cancellationVersion: '2026-11-c',
    checkoutExpiresAt: null,
    lastPaymentAttempt: null,
    checkoutFailureReason: null,
    createdAt: '2026-11-04T09:59:00Z',
    updatedAt: '2026-11-04T09:59:00Z',
    task: {
      __typename: 'Task',
      id: 'task-1',
      title: 'Grocery run for Mama',
      category: null,
      serviceAreaName: 'Dubrovnik',
      address: null,
      accessDetails: null,
      onSiteContact: null,
    },
    helperBusiness: null,
    cancelPreview: null,
    helperCancelPreview: null,
    withdrawalAvailable: false,
    canRequestNoFault: false,
    noFaultRequest: null,
    cancellation: null,
    arrival: { __typename: 'BookingArrival', id: 'booking-1', noShowContestBy: null },
    canRequestPayment: true,
    invoiceRequired: false,
    paymentRequest: null,
    ...extra,
  };
}

async function renderScreen(answer: Record<string, unknown>, result?: Record<string, unknown>) {
  const sent: Record<string, unknown>[] = [];
  const link = new ApolloLink(
    (operation) =>
      new Observable((observer) => {
        if (operation.operationName === 'RequestPayment') {
          sent.push(operation.variables.input as Record<string, unknown>);
          observer.next({ data: { requestPayment: result ?? answer } });
        } else {
          observer.next({ data: { booking: answer } });
        }
        observer.complete();
      }),
  );
  const client = new ApolloClient({ cache: new InMemoryCache({ typePolicies }), link });
  await render(
    <SafeAreaProvider initialMetrics={metrics}>
      <ApolloProvider client={client}>
        <RequestPaymentScreen bookingId="booking-1" onBack={jest.fn()} />
      </ApolloProvider>
    </SafeAreaProvider>,
  );
  await screen.findByTestId('request-payment-screen');
  return sent;
}

beforeEach(async () => {
  await i18n.changeLanguage('en');
});

afterAll(async () => {
  await i18n.changeLanguage('hr');
});

it('sends a private helper’s note, with no invoice step', async () => {
  const sent = await renderScreen(booking(), booking({ status: 'PAYMENT_REQUESTED' }));
  await screen.findByTestId('request-payment-photos');
  expect(screen.queryByTestId('request-payment-invoice')).toBeNull();

  await fireEvent.changeText(screen.getByTestId('request-payment-note'), ' All delivered ');
  await fireEvent.press(screen.getByTestId('request-payment-submit'));

  await screen.findByTestId('payment-requested');
  expect(sent).toEqual([{ bookingId: 'booking-1', note: 'All delivered', photoIds: null }]);
  expect(
    screen.getByText(`Waiting for the poster to release ${formatMoney(3_000, 'en')}.`),
  ).toBeOnTheScreen();
});

it('asks a registered business for its invoice before sending', async () => {
  const sent = await renderScreen(booking({ invoiceRequired: true }));
  await screen.findByTestId('request-payment-invoice');

  await fireEvent.press(screen.getByTestId('request-payment-submit'));
  expect(screen.getByTestId('request-payment-jir-error')).toHaveTextContent(
    en.problems.invoiceMissing,
  );

  await fireEvent.changeText(screen.getByTestId('request-payment-jir'), 'abc');
  await fireEvent.press(screen.getByTestId('request-payment-submit'));
  expect(screen.getByTestId('request-payment-jir-error')).toHaveTextContent(en.problems.jirInvalid);
  expect(sent).toEqual([]);

  await fireEvent.changeText(screen.getByTestId('request-payment-jir'), JIR);
  await fireEvent.press(screen.getByTestId('request-payment-submit'));
  expect(sent).toEqual([{ bookingId: 'booking-1', note: null, photoIds: null, invoiceJir: JIR }]);
});

it('explains when payment can’t be requested yet', async () => {
  await renderScreen(booking({ canRequestPayment: false }));

  expect(await screen.findByTestId('request-payment-unavailable')).toHaveTextContent(
    en.unavailable,
  );
  expect(screen.queryByTestId('request-payment-submit')).toBeNull();
});

it.each(locales)('has every text in %s', async (locale) => {
  await i18n.changeLanguage(locale);
  await renderScreen(booking({ invoiceRequired: true }));

  await screen.findByTestId('request-payment-invoice');
  const catalogue = catalogues[locale].requestPayment;
  expect(screen.getByText(catalogue.invoiceTitle)).toBeOnTheScreen();
  expect(screen.getByText(catalogue.photosTitle)).toBeOnTheScreen();
});

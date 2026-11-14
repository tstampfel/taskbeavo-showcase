import { ApolloClient, ApolloLink, InMemoryCache } from '@apollo/client';
import { ApolloProvider } from '@apollo/client/react';
import { BookingDetailDocument, ViewerDocument, typePolicies } from '@taskbeavo/graphql';
import { catalogues, formatMoney } from '@taskbeavo/i18n';
import { act, fireEvent, render, screen } from '@testing-library/react-native';
import { Dimensions, Linking, StyleSheet } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { Observable, type Subscriber } from 'rxjs';
import { i18n } from '../src/i18n';
import { wordsFit } from '../src/components/ui/heading';
import { BookingDetailScreen, stepsStacked } from '../src/screens/booking-detail-screen';
import { topSafeAreaEdge } from '../src/testing/safe-area';

const metrics = {
  frame: { x: 0, y: 0, width: 390, height: 844 },
  insets: { top: 47, left: 0, right: 0, bottom: 34 },
};
const en = catalogues.en.bookingDetail;
const ADDRESS = 'Ulica Ante Starčevića 24, Gruž';

const task = (exact: boolean) => ({
  __typename: 'Task',
  id: 'task-1',
  title: 'Grocery run for Mama',
  category: null,
  serviceAreaName: 'Dubrovnik',
  address: exact ? ADDRESS : null,
  accessDetails: exact ? 'Gate 4417' : null,
  onSiteContact: exact
    ? {
        __typename: 'OnSiteContact',
        id: 'contact-1',
        name: 'Mama Kata',
        area: 'Gruž',
        language: 'HR',
        phone: '+385912345678',
      }
    : null,
});

function booking(
  helperUserId: string,
  { exact = true, ...extra }: { exact?: boolean } & Record<string, unknown> = {},
) {
  return {
    __typename: 'Booking',
    id: 'booking-1',
    taskId: 'task-1',
    offerId: 'offer-1',
    helperUserId,
    helperIsTrader: false,
    status: 'FUNDS_HELD',
    amountCents: 3_000,
    feeCents: 360,
    creditCents: 0,
    totalCents: 3_360,
    slotStart: '2026-11-08T09:00:00Z',
    termsVersion: 'draft-1',
    cancellationVersion: '2026-11-c',
    checkoutExpiresAt: null,
    lastPaymentAttempt: null,
    checkoutFailureReason: null,
    createdAt: '2026-11-04T09:59:00Z',
    updatedAt: '2026-11-04T09:59:00Z',
    task: task(exact),
    helperBusiness: null,
    cancelPreview: null,
    helperCancelPreview: null,
    withdrawalAvailable: false,
    canRequestNoFault: false,
    noFaultRequest: null,
    cancellation: null,
    arrival: { __typename: 'BookingArrival', id: 'booking-1', noShowContestBy: null },
    ...extra,
  };
}

const VIEWER = {
  __typename: 'User',
  id: 'user-1',
  email: 'ana@example.com',
  firstName: 'Ana',
  lastName: 'Horvat',
  locale: 'en',
  country: 'AT',
  timeZone: 'Europe/Vienna',
  phoneVerified: true,
  phoneNumberMasked: null,
};

async function renderDetail(answer: Record<string, unknown>, onOpen = jest.fn()) {
  let live: Subscriber<unknown> | undefined;
  const link = new ApolloLink(
    (operation) =>
      new Observable((observer) => {
        if (operation.operationName === 'BookingDetailUpdated') {
          live = observer;
          return;
        }
        const data =
          operation.operationName === 'Viewer' ? { viewer: VIEWER } : { booking: answer };
        observer.next({ data });
        observer.complete();
      }),
  );
  const client = new ApolloClient({ cache: new InMemoryCache({ typePolicies }), link });
  await render(
    <SafeAreaProvider initialMetrics={metrics}>
      <ApolloProvider client={client}>
        <BookingDetailScreen bookingId="booking-1" onOpen={onOpen} />
      </ApolloProvider>
    </SafeAreaProvider>,
  );
  await screen.findByTestId('booking-place');
  return {
    onOpen,
    push: async (event: Record<string, unknown>) => {
      if (!live) throw new Error('Not subscribed');
      await act(async () => live?.next({ data: { bookingUpdated: event } }));
    },
  };
}

beforeEach(async () => {
  await i18n.changeLanguage('en');
});

it('shows the active helper the exact place, access details and contact', async () => {
  await renderDetail(booking('user-1'));

  expect(screen.getByTestId('booking-address')).toHaveTextContent(ADDRESS);
  expect(screen.getByText(en.place.whileBooked)).toBeOnTheScreen();
  expect(screen.getByTestId('booking-access')).toHaveTextContent('Access: Gate 4417');
  expect(screen.getByText('On-site contact: Mama Kata, Gruž')).toBeOnTheScreen();
  expect(screen.getByTestId('booking-amount')).toHaveTextContent(
    `You get ${formatMoney(3_000, 'en')}`,
  );
  // The helper never sees who the poster contracts with, nor the poster's total.
  expect(screen.queryByTestId('booking-helper')).toBeNull();
});

it('gives the active helper a Call link to the on-site contact (mockups 5c, 7b, 18a, 18g)', async () => {
  const openURL = jest.spyOn(Linking, 'openURL').mockResolvedValue(undefined);
  try {
    await renderDetail(booking('user-1'));

    await fireEvent.press(screen.getByRole('button', { name: 'Call +385912345678' }));
    expect(openURL).toHaveBeenCalledWith('tel:+385912345678');
  } finally {
    openURL.mockRestore();
  }
});

it('hides the address again when a live event closes the booking', async () => {
  const { push } = await renderDetail(booking('user-1', { status: 'PAYMENT_REQUESTED' }));
  expect(screen.getByTestId('booking-address')).toBeOnTheScreen();

  await push(booking('user-1', { status: 'RELEASED', exact: false }));
  await screen.findByText(en.status.RELEASED);

  expect(screen.queryByTestId('booking-address')).toBeNull();
  expect(screen.queryByTestId('booking-access')).toBeNull();
  expect(screen.queryByText(/Mama Kata/)).toBeNull();
  expect(screen.queryByText(/Call \+385/)).toBeNull();
  expect(screen.getByTestId('booking-area')).toHaveTextContent('Area: Dubrovnik');
  expect(screen.getByText(en.place.ended)).toBeOnTheScreen();
  expect(screen.getByTestId('booking-status')).toHaveTextContent(en.status.RELEASED);
});

it('waits for the server before showing the helper an address left in memory', async () => {
  // Another booking screen left the task in the cache; the booking has since ended.
  const link = new ApolloLink(
    (operation) =>
      new Observable(() => {
        if (operation.operationName === 'BookingDetailUpdated') return;
      }),
  );
  const client = new ApolloClient({ cache: new InMemoryCache({ typePolicies }), link });
  client.writeQuery({ query: ViewerDocument, data: { viewer: VIEWER } as never });
  client.writeQuery({
    query: BookingDetailDocument,
    variables: { id: 'booking-1' },
    data: { booking: booking('user-1') } as never,
  });

  await render(
    <SafeAreaProvider initialMetrics={metrics}>
      <ApolloProvider client={client}>
        <BookingDetailScreen bookingId="booking-1" onOpen={jest.fn()} />
      </ApolloProvider>
    </SafeAreaProvider>,
  );

  await screen.findByTestId('booking-area');
  expect(screen.queryByTestId('booking-address')).toBeNull();
  expect(screen.queryByTestId('booking-access')).toBeNull();
});

it('follows the stepper of mockup 05', async () => {
  await renderDetail(booking('helper-9', { status: 'PAYMENT_REQUESTED' }));

  expect(screen.getByTestId('booking-step-held-done')).toBeOnTheScreen();
  expect(screen.getByTestId('booking-step-requested-current')).toBeOnTheScreen();
  expect(screen.getByTestId('booking-step-released-todo')).toBeOnTheScreen();
});

it.each([
  [1, 'row'],
  [3.1, 'column'],
])('at text scale %s lays the steps out as a %s', async (fontScale, direction) => {
  // Four columns are too narrow for whole words at the largest sizes: the steps become a list.
  const dimensions = jest
    .spyOn(Dimensions, 'get')
    .mockReturnValue({ width: 402, height: 874, scale: 3, fontScale });
  try {
    await renderDetail(booking('helper-9', { status: 'PAYMENT_REQUESTED' }));
    const style = StyleSheet.flatten(screen.getByTestId('booking-steps').props.style);
    expect(style.flexDirection ?? 'column').toBe(direction);
    // Beside a wrapping label the dot keeps its size.
    expect(
      StyleSheet.flatten(screen.getByTestId('booking-step-dot-held').props.style),
    ).toMatchObject({ width: 20, flexShrink: 0 });
  } finally {
    dimensions.mockRestore();
  }
});

it('sends the poster to release once payment is requested', async () => {
  const { onOpen } = await renderDetail(booking('helper-9', { status: 'PAYMENT_REQUESTED' }));

  await fireEvent.press(screen.getByTestId('booking-link-release'));

  expect(onOpen).toHaveBeenCalledWith('release');
  expect(screen.queryByTestId('booking-link-arrival')).toBeNull();
});

it('links both sides to the day of the job while the money is held', async () => {
  const { onOpen } = await renderDetail(booking('user-1'));

  await fireEvent.press(screen.getByTestId('booking-link-arrival'));

  expect(onOpen).toHaveBeenCalledWith('arrival');
});

it("shows the poster a trader helper's confirmed name and OIB", async () => {
  await renderDetail(
    booking('helper-9', {
      helperIsTrader: true,
      helperBusiness: {
        __typename: 'BookingTrader',
        id: 'booking-1',
        businessName: 'Vrtlarski obrt Horvat',
        businessOib: '00000000124',
      },
    }),
  );

  expect(screen.getByTestId('trader-identity')).toBeOnTheScreen();
  expect(screen.getByText('Vrtlarski obrt Horvat · OIB 00000000124')).toBeOnTheScreen();
  // The registered address stays on the offer and the checkout (hard rule e).
  expect(screen.queryByText(/Registered business address/)).toBeNull();
  expect(screen.getByText(catalogues.en.checkout.held.email)).toBeOnTheScreen();
});

it('shows the poster a card decline from the live checkout and the way back to pay', async () => {
  const expires = new Date(Date.now() + 20 * 60_000).toISOString();
  const { onOpen, push } = await renderDetail(
    booking('helper-9', { status: 'AWAITING_PAYMENT', checkoutExpiresAt: expires, exact: false }),
  );

  await push(
    booking('helper-9', {
      status: 'AWAITING_PAYMENT',
      checkoutExpiresAt: expires,
      exact: false,
      lastPaymentAttempt: {
        __typename: 'PaymentAttempt',
        id: 'attempt-1',
        kind: 'DECLINED',
        declineCode: 'insufficient_funds',
        at: new Date().toISOString(),
      },
    }),
  );

  await screen.findByText(catalogues.en.checkout.decline.insufficient_funds);
  await fireEvent.press(screen.getByTestId('booking-pay'));
  expect(onOpen).toHaveBeenCalledWith('checkout');
});

it('tells the helper the address comes once the payment is confirmed', async () => {
  await renderDetail(booking('user-1', { status: 'AWAITING_PAYMENT', exact: false }));

  expect(screen.getByText(en.place.afterPayment)).toBeOnTheScreen();
  expect(screen.getByText(en.checkout.waiting)).toBeOnTheScreen();
  expect(screen.queryByTestId('booking-pay')).toBeNull();
});

it.each(['hr', 'de', 'fr'] as const)('speaks %s', async (locale) => {
  await i18n.changeLanguage(locale);
  await renderDetail(booking('user-1'));

  expect(screen.getByTestId('booking-status')).toHaveTextContent(
    catalogues[locale].bookingDetail.status.FUNDS_HELD,
  );
  expect(screen.getByText(catalogues[locale].bookingDetail.place.whileBooked)).toBeOnTheScreen();
});

// BEAV-201: the stack header is hidden, so the title must clear the status bar and notch.
it('keeps the title below the status bar', async () => {
  await renderDetail(booking('user-1'));
  const title = screen.getByRole('header', { name: 'Grocery run for Mama' });
  expect(topSafeAreaEdge(title)).toBe('additive');
});

describe('at large text (BEAV-223)', () => {
  function atFontScale(fontScale: number) {
    return jest
      .spyOn(Dimensions, 'get')
      .mockReturnValue({ width: 402, height: 874, scale: 3, fontScale });
  }
  afterEach(async () => {
    jest.restoreAllMocks();
    await i18n.changeLanguage('en');
  });

  it('wraps a step label beside its dot instead of running past the edge, in French', async () => {
    await i18n.changeLanguage('fr');
    atFontScale(53 / 17);
    await renderDetail(booking('helper-9', { status: 'FUNDS_HELD' }));
    const label = screen.getByTestId('booking-step-label-held');
    expect(StyleSheet.flatten(label.props.style)).toMatchObject({ flexShrink: 1 });
    // Its longest word ("Paiement") fits the line beside the dot at the size it renders.
    const fontSize = StyleSheet.flatten(label.props.style).fontSize as number;
    const scale = Math.min(53 / 17, label.props.maxFontSizeMultiplier as number);
    expect(wordsFit(label.props.children as string, fontSize * scale, 402 - 2 * 16 - 20 - 8)).toBe(
      true,
    );
  });

  it('lists the steps once a label’s word no longer fits its column', () => {
    const fr = catalogues.fr.bookingDetail.steps;
    const labels = [fr.accepted, fr.held, fr.requested, fr.released];
    expect(stepsStacked(labels, 402, 1)).toBe(false);
    expect(stepsStacked(labels, 402, 1.3)).toBe(true);
    // A narrow phone at a modest size: "Paiement" no longer fits a quarter of the line.
    const column = (320 - 2 * 16) / 4;
    expect(stepsStacked(labels, 320, 1.25)).toBe(
      !labels.every((label) => wordsFit(label, 12 * 1.25, column)),
    );
  });

  it.each([
    [1, 'row'],
    [2, 'column'],
  ])('at text scale %s puts the status badge in a %s with the start time', async (scale, dir) => {
    atFontScale(scale);
    await renderDetail(booking('helper-9', { status: 'FUNDS_HELD' }));
    const style = StyleSheet.flatten(screen.getByTestId('booking-starts').props.style);
    expect(style.flexDirection ?? 'column').toBe(dir);
  });
});

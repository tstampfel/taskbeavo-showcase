import { ApolloClient, ApolloLink, InMemoryCache } from '@apollo/client';
import { ApolloProvider } from '@apollo/client/react';
import { typePolicies } from '@taskbeavo/graphql';
import { catalogues, formatMoney, locales, type Locale } from '@taskbeavo/i18n';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import type { ReactNode } from 'react';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { Observable, type Subscriber } from 'rxjs';
import { i18n } from '../src/i18n';
import type { PaymentSheet } from '../src/payments/stripe-config';
import { ReleaseScreen } from '../src/screens/release-screen';

const metrics = {
  frame: { x: 0, y: 0, width: 390, height: 844 },
  insets: { top: 47, left: 0, right: 0, bottom: 34 },
};
const en = catalogues.en.release;
const POSTER = 'user-poster';
const HELPER = 'user-helper';
const euros = (cents: number, locale: Locale = 'en') => formatMoney(cents, locale);

function booking(extra: Record<string, unknown> = {}) {
  return {
    __typename: 'Booking',
    id: 'booking-1',
    taskId: 'task-1',
    offerId: 'offer-1',
    helperUserId: HELPER,
    helperIsTrader: false,
    status: 'PAYMENT_REQUESTED',
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
    arrival: {
      __typename: 'BookingArrival',
      id: 'booking-1',
      noShowContestBy: null,
      canAnswerLate: false,
      canConfirmCame: false,
      canConfirmNoShow: false,
    },
    canRequestPayment: false,
    invoiceRequired: false,
    paymentRequest: {
      __typename: 'PaymentRequest',
      id: 'pr-1',
      requestedAt: '2026-11-08T10:10:00Z',
      note: 'All delivered. Mama says hvala!',
      invoiceJir: null,
      invoice: null,
      photos: [
        {
          __typename: 'BookingPhoto',
          id: 'ph-1',
          url: 'https://r2.example.com/1',
          width: 800,
          height: 600,
        },
        {
          __typename: 'BookingPhoto',
          id: 'ph-2',
          url: 'https://r2.example.com/2',
          width: 800,
          height: 600,
        },
      ],
    },
    canReleasePayment: true,
    releaseProposal: null,
    tip: null,
    ...extra,
  };
}

const proposal = (status: string, extra: Record<string, unknown> = {}) => ({
  __typename: 'ReleaseProposal',
  id: 'rp-1',
  releasedCents: 2_000,
  status,
  refundCents: 1_120,
  createdAt: '2026-11-08T11:00:00Z',
  answeredAt: null,
  ...extra,
});

const viewer = (id: string) => ({
  __typename: 'User',
  id,
  email: 'someone@example.com',
  firstName: 'Ana',
  lastName: 'Horvat',
  locale: 'en',
  country: 'AT',
  timeZone: 'Europe/Vienna',
  phoneVerified: true,
  phoneNumberMasked: null,
});

interface Setup {
  viewerId?: string;
  /** Answers by operation name; the booking query answers with `answer`. */
  results?: Record<string, (variables: Record<string, unknown>) => unknown>;
  sheet?: PaymentSheet | null;
}

async function renderRelease(answer: Record<string, unknown>, setup: Setup = {}) {
  const sent: { name: string; variables: Record<string, unknown> }[] = [];
  let live: Subscriber<unknown> | undefined;
  // As on the server: a mutation's booking is what the next query returns.
  let current = answer;
  const link = new ApolloLink(
    (operation) =>
      new Observable((observer) => {
        const name = operation.operationName ?? '';
        if (name === 'BookingDetailUpdated') {
          live = observer;
          return;
        }
        sent.push({ name, variables: operation.variables });
        const custom = setup.results?.[name];
        if (custom) {
          const result = custom(operation.variables);
          if (result instanceof Error) observer.error(result);
          else {
            const data = (result as { data?: Record<string, { __typename?: string }> }).data;
            const updated = Object.values(data ?? {}).find((v) => v?.__typename === 'Booking');
            if (updated) current = updated as Record<string, unknown>;
            observer.next(result as never);
          }
        } else if (name === 'Viewer') {
          observer.next({ data: { viewer: viewer(setup.viewerId ?? POSTER) } });
        } else {
          observer.next({ data: { booking: current } });
        }
        observer.complete();
      }),
  );
  const client = new ApolloClient({ cache: new InMemoryCache({ typePolicies }), link });
  const scope = ({ children }: { children: (sheet: PaymentSheet | null) => ReactNode }) =>
    children(setup.sheet === undefined ? null : setup.sheet);
  await render(
    <SafeAreaProvider initialMetrics={metrics}>
      <ApolloProvider client={client}>
        <ReleaseScreen bookingId="booking-1" onBack={jest.fn()} PaymentScope={scope} />
      </ApolloProvider>
    </SafeAreaProvider>,
  );
  await screen.findByTestId('release-screen');
  return {
    sent,
    push: async (event: Record<string, unknown>) => {
      if (!live) throw new Error('Not subscribed');
      await act(async () => live?.next({ data: { bookingUpdated: event } }));
    },
  };
}

beforeEach(async () => {
  await i18n.changeLanguage('en');
});

it.each(locales)(
  'shows the poster the photos, note, amount and no preselected tip in %s (7d)',
  async (locale) => {
    await i18n.changeLanguage(locale);
    await renderRelease(booking());
    const messages = catalogues[locale].release;

    expect(await screen.findByText(messages.finished)).toBeOnTheScreen();
    expect(screen.getByText('All delivered. Mama says hvala!')).toBeOnTheScreen();
    expect(screen.getByLabelText(messages.photoLabel.replace('{number}', '1'))).toBeOnTheScreen();
    expect(screen.getByLabelText(messages.photoLabel.replace('{number}', '2'))).toBeOnTheScreen();
    expect(screen.getByTestId('release-amount')).toHaveTextContent(euros(3_000, locale), {
      exact: false,
    });
    for (const cents of [200, 500, 1_000]) {
      expect(screen.getByTestId(`release-tip-${cents}`)).not.toBeChecked();
    }
    expect(screen.getByTestId('release-tip-none')).toBeChecked();
    expect(screen.getByTestId('release-submit')).toHaveTextContent(messages.submit, {
      exact: false,
    });
  },
);

it('releases after the confirmation, which sums the price and the tip (7e, 7f)', async () => {
  const sheet: PaymentSheet = { pay: jest.fn().mockResolvedValue('paid') };
  const { sent } = await renderRelease(booking(), {
    sheet,
    results: {
      ReleasePayment: () => ({
        data: { releasePayment: booking({ status: 'RELEASED', canReleasePayment: false }) },
      }),
      AddTip: () => ({
        data: {
          addTip: {
            __typename: 'TipSession',
            id: 'tip-1',
            clientSecret: 'pi_123_secret_456',
            tip: { __typename: 'Tip', id: 'tip-1', amountCents: 500, status: 'PENDING' },
          },
        },
      }),
    },
  });
  await screen.findByText(en.finished);

  await fireEvent.press(screen.getByTestId('release-tip-500'));
  await fireEvent.press(screen.getByTestId('release-submit'));

  expect(screen.getByText(`Release ${euros(3_500)} to your helper?`)).toBeOnTheScreen();
  expect(screen.getByTestId('release-confirm-tip')).toHaveTextContent(euros(500), { exact: false });
  // Nothing is sent before the confirmation.
  expect(sent.some(({ name }) => name === 'ReleasePayment')).toBe(false);

  await fireEvent.press(screen.getByTestId('release-confirm-release'));

  expect(await screen.findByText(en.released.title)).toBeOnTheScreen();
  await waitFor(() =>
    expect(sheet.pay).toHaveBeenCalledWith('pi_123_secret_456', en.tip.sheetButton),
  );
  expect(sent.find(({ name }) => name === 'ReleasePayment')?.variables).toEqual({
    bookingId: 'booking-1',
  });
  expect(sent.find(({ name }) => name === 'AddTip')?.variables).toEqual({
    bookingId: 'booking-1',
    amountCents: 500,
  });
  // Stripe took the tip; it counts once the webhook says so.
  expect(await screen.findByTestId('release-tip-confirming')).toHaveTextContent(en.tip.confirming, {
    exact: false,
  });
});

it('releases without a tip and pays nothing else', async () => {
  const sheet: PaymentSheet = { pay: jest.fn() };
  const { sent } = await renderRelease(booking(), {
    sheet,
    results: {
      ReleasePayment: () => ({
        data: { releasePayment: booking({ status: 'RELEASED', canReleasePayment: false }) },
      }),
    },
  });
  await screen.findByText(en.finished);

  await fireEvent.press(screen.getByTestId('release-submit'));
  expect(screen.queryByTestId('release-confirm-tip')).toBeNull();
  await fireEvent.press(screen.getByTestId('release-confirm-release'));

  expect(await screen.findByText(en.released.title)).toBeOnTheScreen();
  expect(sent.some(({ name }) => name === 'AddTip')).toBe(false);
  expect(sheet.pay).not.toHaveBeenCalled();
  // The tip can still be added afterwards.
  expect(screen.getByTestId('release-tip')).toBeOnTheScreen();
});

it('shows the tip once Stripe confirmed it', async () => {
  await renderRelease(
    booking({
      status: 'PAID_OUT',
      canReleasePayment: false,
      tip: { __typename: 'Tip', id: 'tip-1', amountCents: 500, status: 'SUCCEEDED' },
    }),
  );

  expect(await screen.findByTestId('release-tip-paid')).toHaveTextContent(
    `You tipped ${euros(500)}. Thank you!`,
    { exact: false },
  );
});

it('says plainly when this build cannot take a tip payment', async () => {
  await renderRelease(booking({ status: 'RELEASED', canReleasePayment: false }), { sheet: null });

  await fireEvent.press(await screen.findByTestId('release-tip-200'));
  await fireEvent.press(screen.getByTestId('release-tip-pay'));

  expect(await screen.findByTestId('release-tip-error')).toHaveTextContent(en.tip.notConfigured, {
    exact: false,
  });
});

it('proposes a lower amount after showing what it moves', async () => {
  const { sent } = await renderRelease(booking(), {
    results: {
      PartialReleasePreview: () => ({
        data: {
          partialReleasePreview: {
            __typename: 'PartialReleasePreview',
            id: 'booking-1',
            releasedCents: 2_000,
            keptFeeCents: 240,
            refundCents: 1_120,
          },
        },
      }),
      ProposePartialRelease: () => ({
        data: {
          proposePartialRelease: booking({ releaseProposal: proposal('OPEN') }),
        },
      }),
    },
  });
  await screen.findByText(en.finished);

  await fireEvent.press(screen.getByTestId('release-lower'));
  await fireEvent.changeText(screen.getByTestId('release-partial-amount'), '30');
  await fireEvent.press(screen.getByTestId('release-partial-check'));
  expect(
    screen.getByText(en.partial.problems.range.replace('{price}', euros(3_000))),
  ).toBeOnTheScreen();

  await fireEvent.changeText(screen.getByTestId('release-partial-amount'), '20');
  await fireEvent.press(screen.getByTestId('release-partial-check'));
  expect(await screen.findByTestId('release-partial-refund')).toHaveTextContent(euros(1_120), {
    exact: false,
  });

  await fireEvent.press(screen.getByTestId('release-partial-send'));

  expect(await screen.findByTestId('release-proposal-open')).toBeOnTheScreen();
  expect(sent.find(({ name }) => name === 'ProposePartialRelease')?.variables).toEqual({
    bookingId: 'booking-1',
    releasedCents: 2_000,
  });
  expect(screen.getByTestId('release-withdraw')).toBeOnTheScreen();
});

it('tells the poster the helper declined, live', async () => {
  const { push } = await renderRelease(booking({ releaseProposal: proposal('OPEN') }));
  expect(await screen.findByTestId('release-proposal-open')).toBeOnTheScreen();

  await push(booking({ releaseProposal: proposal('DECLINED') }));

  expect(await screen.findByTestId('release-declined')).toHaveTextContent(
    `Your helper declined ${euros(2_000)}.`,
    { exact: false },
  );
  expect(screen.getByTestId('release-submit')).toBeOnTheScreen();
});

it.each(locales)('lets the helper accept or decline a lower amount in %s', async (locale) => {
  await i18n.changeLanguage(locale);
  const { sent } = await renderRelease(
    booking({ canReleasePayment: false, releaseProposal: proposal('OPEN', { refundCents: null }) }),
    {
      viewerId: HELPER,
      results: {
        RespondToPartialRelease: () => ({
          data: {
            respondToPartialRelease: booking({
              canReleasePayment: false,
              releaseProposal: proposal('ACCEPTED', { refundCents: null }),
            }),
          },
        }),
      },
    },
  );
  const messages = catalogues[locale].release;

  expect(await screen.findByText(messages.answer.title)).toBeOnTheScreen();
  expect(screen.getByTestId('release-decline')).toHaveTextContent(messages.answer.decline, {
    exact: false,
  });
  await fireEvent.press(screen.getByTestId('release-accept'));

  expect(await screen.findByTestId('release-partial-settled')).toBeOnTheScreen();
  expect(sent.find(({ name }) => name === 'RespondToPartialRelease')?.variables).toEqual({
    bookingId: 'booking-1',
    accept: true,
  });
});

it('never offers the helper Release payment', async () => {
  await renderRelease(booking(), { viewerId: HELPER });

  expect(await screen.findByTestId('release-helper-waiting')).toHaveTextContent(
    `Waiting for the poster to release ${euros(3_000)}.`,
    { exact: false },
  );
  expect(screen.queryByTestId('release-submit')).toBeNull();
});

it('says when the helper has not requested payment yet', async () => {
  await renderRelease(
    booking({ status: 'FUNDS_HELD', canReleasePayment: false, paymentRequest: null }),
  );

  expect(await screen.findByTestId('release-not-requested')).toHaveTextContent(en.notRequested, {
    exact: false,
  });
  expect(screen.queryByTestId('release-submit')).toBeNull();
});

import { ApolloClient, ApolloLink, InMemoryCache } from '@apollo/client';
import { ApolloProvider } from '@apollo/client/react';
import { typePolicies } from '@taskbeavo/graphql';
import { catalogues, formatMoney } from '@taskbeavo/i18n';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { Observable } from 'rxjs';
import { i18n } from '../src/i18n';
import { CancelScreen } from '../src/screens/cancel-screen';

const metrics = {
  frame: { x: 0, y: 0, width: 390, height: 844 },
  insets: { top: 47, left: 0, right: 0, bottom: 34 },
};
const en = catalogues.en.cancel;
const ENDS = '2026-11-19T23:00:00+00:00';

const preview = (extra: Record<string, unknown> = {}) => ({
  __typename: 'CancellationPreview',
  id: 'booking-1',
  case: 'LATE',
  chargeCents: 3_360,
  refundCents: 2_520,
  helperCompensationCents: 750,
  keptFeeCents: 90,
  creditBackCents: 0,
  refusedWith: null,
  ...extra,
});

function booking(extra: Record<string, unknown> = {}) {
  return {
    __typename: 'Booking',
    id: 'booking-1',
    taskId: 'task-1',
    offerId: 'offer-1',
    helperUserId: '7',
    helperIsTrader: false,
    status: 'FUNDS_HELD',
    amountCents: 3_000,
    feeCents: 360,
    creditCents: 0,
    totalCents: 3_360,
    slotStart: '2026-11-05T18:00:00Z',
    termsVersion: 'draft-1',
    cancellationVersion: '2026-11-c',
    checkoutExpiresAt: null,
    lastPaymentAttempt: null,
    checkoutFailureReason: null,
    createdAt: '2026-11-04T09:59:00Z',
    updatedAt: '2026-11-04T09:59:00Z',
    withdrawalAvailable: false,
    withdrawalEndsAt: null,
    cancelPreview: preview(),
    cancellation: null,
    helperCancelPreview: null,
    noFaultRequest: null,
    canRequestNoFault: true,
    canReopenTask: false,
    taskReopened: false,
    ...extra,
  };
}

const cancellation = (extra: Record<string, unknown> = {}) => ({
  __typename: 'Cancellation',
  id: 'booking-1',
  withdrawal: false,
  case: 'LATE',
  refundCents: 2_520,
  helperCompensationCents: 750,
  keptFeeCents: 90,
  awaitingSupport: false,
  confirmed: false,
  requestedAt: '2026-11-05T10:00:00Z',
  requestedBy: 'POSTER',
  posterCreditCents: 0,
  creditBackCents: 0,
  ...extra,
});

type Answer = Record<string, unknown> | { error: string };

function client(answers: Record<string, () => Answer>, variables: Record<string, unknown>) {
  const link = new ApolloLink(
    (operation) =>
      new Observable((observer) => {
        const name = operation.operationName ?? '';
        variables[name] = operation.variables;
        const answer = answers[name]?.();
        if (!answer) throw new Error(`No answer for ${name}`);
        if ('error' in answer) {
          observer.next({ errors: [{ message: 'no', extensions: { code: answer.error } }] });
        } else {
          observer.next({ data: answer });
        }
        observer.complete();
      }),
  );
  return new ApolloClient({ cache: new InMemoryCache({ typePolicies }), link });
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

async function renderCancel(given: Record<string, () => Answer>) {
  const answers = { Viewer: () => ({ viewer: VIEWER }), ...given };
  const variables: Record<string, unknown> = {};
  const onOpenPolicy = jest.fn();
  await render(
    <SafeAreaProvider initialMetrics={metrics}>
      <ApolloProvider client={client(answers, variables)}>
        <CancelScreen bookingId="booking-1" onOpenPolicy={onOpenPolicy} onBack={jest.fn()} />
      </ApolloProvider>
    </SafeAreaProvider>,
  );
  return { variables, onOpenPolicy };
}

beforeEach(async () => {
  await i18n.changeLanguage('en');
});

it('shows the server preview and cancels with the chosen reason', async () => {
  const { variables } = await renderCancel({
    BookingCancellation: () => ({ booking: booking() }),
    CancelBooking: () => ({
      cancelBooking: booking({
        status: 'CANCEL_REQUESTED',
        cancelPreview: null,
        cancellation: cancellation(),
      }),
    }),
  });

  await screen.findByText(en.cases.LATE);
  expect(screen.getByText(formatMoney(2_520, 'en'))).toBeTruthy();
  expect(screen.getByText(formatMoney(750, 'en'))).toBeTruthy();
  const confirm = screen.getByTestId('cancel-confirm');
  expect(confirm.props.accessibilityState?.disabled).toBe(true);

  await fireEvent.press(screen.getByTestId('cancel-reason-DIFFERENT_DATE'));
  await fireEvent.press(screen.getByTestId('cancel-confirm'));

  await screen.findByText(en.done.cancelled);
  expect(variables.CancelBooking).toEqual({
    id: 'booking-1',
    reason: 'DIFFERENT_DATE',
    expectedRefundCents: 2_520,
  });
  expect(
    screen.getByText(en.done.refunding.replace('{amount}', formatMoney(2_520, 'en'))),
  ).toBeTruthy();
});

it('keeps the withdrawal button after the start while the cancel is refused', async () => {
  const { variables } = await renderCancel({
    BookingCancellation: () => ({
      booking: booking({
        helperIsTrader: true,
        withdrawalAvailable: true,
        withdrawalEndsAt: ENDS,
        cancelPreview: preview({ case: null, refusedWith: 'WORK_STARTED' }),
      }),
    }),
    WithdrawFromContract: () => ({
      withdrawFromContract: booking({
        status: 'CANCEL_REQUESTED',
        cancelPreview: null,
        cancellation: cancellation({
          withdrawal: true,
          case: null,
          refundCents: null,
          helperCompensationCents: null,
          keptFeeCents: null,
          awaitingSupport: true,
        }),
      }),
    }),
  });

  await screen.findByText(en.refused.WORK_STARTED);
  expect(screen.queryByTestId('cancel-confirm')).toBeNull();
  expect(screen.getByText(en.withdrawal.afterStart)).toBeTruthy();

  await fireEvent.press(screen.getByTestId('withdraw-from-contract'));
  // Nothing is sent before the explicit confirmation.
  expect(variables.WithdrawFromContract).toBeUndefined();
  expect(screen.getByText(en.withdrawal.confirmBody)).toBeTruthy();
  // The statement's name and email are prefilled from the account, and editable.
  expect(screen.getByTestId('withdraw-name').props.value).toBe('Ana Horvat');
  expect(screen.getByTestId('withdraw-email').props.value).toBe('ana@example.com');
  expect(screen.getByText(`${en.withdrawal.contract}: booking-1`)).toBeTruthy();
  await fireEvent.changeText(screen.getByTestId('withdraw-email'), 'ana.h@example.net');
  await fireEvent.press(screen.getByTestId('withdraw-confirm'));

  await screen.findByText(en.done.withdrawn);
  expect(variables.WithdrawFromContract).toEqual({
    id: 'booking-1',
    name: 'Ana Horvat',
    email: 'ana.h@example.net',
  });
  expect(screen.getByText(en.done.awaitingSupport)).toBeTruthy();
  expect(screen.getByText(en.done.emailed)).toBeTruthy();
});

it('shows a refused cancel after the helper requested payment', async () => {
  await renderCancel({
    BookingCancellation: () => ({
      booking: booking({
        status: 'PAYMENT_REQUESTED',
        cancelPreview: preview({ case: null, refusedWith: 'BOOKING_ACTION_NOT_ALLOWED' }),
      }),
    }),
  });

  await screen.findByText(en.refused.BOOKING_ACTION_NOT_ALLOWED);
  expect(screen.queryByTestId('withdraw-from-contract')).toBeNull();
});

it('opens the cancellation policy in the poster’s language', async () => {
  const { onOpenPolicy } = await renderCancel({
    BookingCancellation: () => ({ booking: booking() }),
  });

  await fireEvent.press(await screen.findByText(en.policyLink));

  await waitFor(() => expect(onOpenPolicy).toHaveBeenCalledWith('en'));
});

it('shows the new preview when a deadline passed while the screen was open', async () => {
  let previews = 0;
  await renderCancel({
    BookingCancellation: () => {
      previews += 1;
      return {
        booking: booking({
          cancelPreview:
            previews === 1
              ? preview({
                  case: 'EARLY',
                  refundCents: 3_360,
                  helperCompensationCents: 0,
                  keptFeeCents: 0,
                })
              : preview(),
        }),
      };
    },
    CancelBooking: () => ({ error: 'CANCELLATION_CHANGED' }),
  });

  await screen.findByText(en.cases.EARLY);
  await fireEvent.press(screen.getByTestId('cancel-reason-OTHER'));
  await fireEvent.press(screen.getByTestId('cancel-confirm'));

  await screen.findByText(en.cases.LATE);
  expect(screen.getByText(catalogues.en.errors.CANCELLATION_CHANGED)).toBeTruthy();
});

const HELPER = { ...VIEWER, id: '7', email: 'ivo@example.com', firstName: 'Ivo' };

const noFaultRequest = (extra: Record<string, unknown> = {}) => ({
  __typename: 'NoFaultRequest',
  id: '11',
  reason: null,
  label: 'PERSONAL_EMERGENCY',
  requestedBy: 'POSTER',
  requestedByYou: false,
  requestedAt: '2026-11-05T10:00:00Z',
  respondBy: new Date(Date.now() + 47.5 * 3_600_000).toISOString(),
  response: null,
  awaitingSupport: false,
  canRespond: true,
  decision: null,
  open: true,
  ...extra,
});

it('shows the helper the full refund and the credit, and cancels with their reason', async () => {
  const helperPreview = {
    __typename: 'HelperCancelPreview',
    id: 'booking-1',
    refundCents: 3_360,
    posterCreditCents: 500,
    refusedWith: null,
  };
  const { variables } = await renderCancel({
    Viewer: () => ({ viewer: HELPER }),
    BookingCancellation: () => ({
      booking: booking({ cancelPreview: null, helperCancelPreview: helperPreview }),
    }),
    CancelAsHelper: () => ({
      cancelAsHelper: booking({
        status: 'CANCEL_REQUESTED',
        cancelPreview: null,
        cancellation: cancellation({ case: 'HELPER_CANCEL', requestedBy: 'HELPER' }),
      }),
    }),
  });

  const warning = await screen.findByTestId('helper-cancel-warning');
  expect(warning.props.children).toContain(formatMoney(3_360, 'en'));
  expect(warning.props.children).toContain(formatMoney(500, 'en'));
  expect(screen.getByText(en.helper.neverPay)).toBeTruthy();
  await fireEvent.press(screen.getByTestId('cancel-reason-ILL'));
  await fireEvent.press(screen.getByTestId('helper-cancel-confirm'));

  await waitFor(() => expect(variables.CancelAsHelper).toEqual({ id: 'booking-1', reason: 'ILL' }));
});

it('sends a no-fault request instead of a cancel when a no-fault reason is picked', async () => {
  const { variables } = await renderCancel({
    BookingCancellation: () => ({ booking: booking() }),
    RequestNoFaultCancellation: () => ({
      requestNoFaultCancellation: booking({
        status: 'CANCEL_REQUESTED',
        cancelPreview: null,
        noFaultRequest: noFaultRequest({ requestedByYou: true, canRespond: false }),
      }),
    }),
  });

  await fireEvent.press(await screen.findByTestId('cancel-reason-WEATHER'));
  expect(screen.queryByTestId('cancel-confirm')).toBeNull();
  await fireEvent.press(screen.getByTestId('no-fault-send'));

  await waitFor(() =>
    expect(variables.RequestNoFaultCancellation).toEqual({ id: 'booking-1', reason: 'WEATHER' }),
  );
  await screen.findByText(en.pending.waiting);
  expect(screen.getByText(/48 h left/)).toBeTruthy();
});

it('lets the poster still agree to a helper’s request while support decides', async () => {
  const atSupport = noFaultRequest({
    requestedBy: 'HELPER',
    response: 'NO_REPLY',
    awaitingSupport: true,
    respondBy: new Date(Date.now() - 3_600_000).toISOString(),
  });
  const { variables } = await renderCancel({
    BookingCancellation: () => ({
      booking: booking({
        status: 'CANCEL_REQUESTED',
        cancelPreview: null,
        noFaultRequest: atSupport,
      }),
    }),
    RespondToNoFaultCancellation: () => ({
      respondToNoFaultCancellation: booking({
        status: 'CANCEL_REQUESTED',
        cancelPreview: null,
        noFaultRequest: { ...atSupport, open: false, response: 'AGREED', canRespond: false },
        cancellation: cancellation({ case: 'NO_FAULT', refundCents: 3_360 }),
      }),
    }),
  });

  await screen.findByText(en.pending.underReview);
  expect(screen.getByText(en.pending.underReviewBody)).toBeTruthy();
  expect(screen.getByText(en.pending.posterAnswerUnderReview)).toBeTruthy();
  await fireEvent.press(screen.getByTestId('no-fault-agree'));

  await waitFor(() =>
    expect(variables.RespondToNoFaultCancellation).toEqual({ id: 'booking-1', agree: true }),
  );
});

it('offers no answer once the poster has answered and support decides', async () => {
  await renderCancel({
    BookingCancellation: () => ({
      booking: booking({
        status: 'CANCEL_REQUESTED',
        cancelPreview: null,
        noFaultRequest: noFaultRequest({
          requestedBy: 'HELPER',
          response: 'DISAGREED',
          awaitingSupport: true,
          canRespond: false,
        }),
      }),
    }),
  });

  await screen.findByText(en.pending.underReviewBody);
  expect(screen.queryByTestId('no-fault-agree')).toBeNull();
  expect(screen.queryByText(en.pending.waiting)).toBeNull();
});

it('lets the helper agree to the poster’s no-fault request', async () => {
  const { variables } = await renderCancel({
    Viewer: () => ({ viewer: HELPER }),
    BookingCancellation: () => ({
      booking: booking({
        status: 'CANCEL_REQUESTED',
        cancelPreview: null,
        noFaultRequest: noFaultRequest(),
      }),
    }),
    RespondToNoFaultCancellation: () => ({
      respondToNoFaultCancellation: booking({
        status: 'CANCEL_REQUESTED',
        cancelPreview: null,
        noFaultRequest: noFaultRequest({ open: false, response: 'AGREED', canRespond: false }),
        cancellation: cancellation({ case: 'NO_FAULT', refundCents: 3_360 }),
      }),
    }),
  });

  await screen.findByText(en.pending.helperAnswer);
  // Decision 4: the helper sees "a personal emergency", never the specific reason.
  expect(
    screen.getByText(
      en.pending.posterAsked.replace('{reason}', en.noFaultLabels.PERSONAL_EMERGENCY),
    ),
  ).toBeTruthy();
  await fireEvent.press(screen.getByTestId('no-fault-agree'));

  await waitFor(() =>
    expect(variables.RespondToNoFaultCancellation).toEqual({ id: 'booking-1', agree: true }),
  );
});

it('shows the poster the credit and re-opens the task after a helper cancel', async () => {
  const done = cancellation({
    case: 'HELPER_CANCEL',
    requestedBy: 'HELPER',
    refundCents: 3_360,
    helperCompensationCents: 0,
    keptFeeCents: 0,
    confirmed: true,
    posterCreditCents: 500,
    creditBackCents: 360,
  });
  const { variables } = await renderCancel({
    BookingCancellation: () => ({
      booking: booking({
        status: 'REFUNDED',
        cancelPreview: null,
        cancellation: done,
        canReopenTask: true,
      }),
    }),
    ReopenTask: () => ({
      reopenTask: booking({
        status: 'REFUNDED',
        cancelPreview: null,
        cancellation: done,
        taskReopened: true,
      }),
    }),
  });

  await screen.findByText(en.done.byHelper);
  expect(screen.getByTestId('cancel-credit')).toBeTruthy();
  expect(screen.getByTestId('cancel-credit-back')).toBeTruthy(); // the credit it used
  await fireEvent.press(screen.getByTestId('cancel-reopen'));

  await waitFor(() => expect(variables.ReopenTask).toEqual({ bookingId: 'booking-1' }));
  await screen.findByText(en.done.reopened);
});

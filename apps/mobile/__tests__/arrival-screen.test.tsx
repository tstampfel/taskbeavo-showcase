import { ApolloClient, ApolloLink, InMemoryCache } from '@apollo/client';
import { ApolloProvider } from '@apollo/client/react';
import { typePolicies } from '@taskbeavo/graphql';
import { catalogues, formatMoney } from '@taskbeavo/i18n';
import { fireEvent, render, screen } from '@testing-library/react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { Observable } from 'rxjs';
import { i18n } from '../src/i18n';
import { ArrivalScreen } from '../src/screens/arrival-screen';

const metrics = {
  frame: { x: 0, y: 0, width: 390, height: 844 },
  insets: { top: 47, left: 0, right: 0, bottom: 34 },
};
const en = catalogues.en.arrival;

const arrival = (extra: Record<string, unknown> = {}) => ({
  __typename: 'BookingArrival',
  id: 'booking-1',
  onMyWayAt: null,
  etaAt: null,
  lateReportedAt: null,
  lateAcceptedAt: null,
  arrivedAt: null,
  posterConfirmedCameAt: null,
  noShowConfirmedAt: null,
  onTheWayPromptSentAt: null,
  posterAskedAt: null,
  noShowDueAt: null,
  noShowContestBy: null,
  noShowContestedAt: null,
  noShowSettledAt: null,
  noShowAwaitingSupport: false,
  noShowRejected: false,
  noShowRefundCents: null,
  noShowCreditCents: null,
  earlyStartRequestedAt: null,
  earlyStartConfirmedAt: null,
  earlyStartDeclinedAt: null,
  earlyStartPending: false,
  canTap: false,
  canRequestEarlyStart: false,
  canAnswerLate: false,
  canConfirmCame: false,
  canConfirmNoShow: false,
  canContestNoShow: false,
  canAnswerEarlyStart: false,
  earlyStartWithdrawal: false,
  ...extra,
});

function booking(helperUserId: string, extra: Record<string, unknown> = {}) {
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
    arrival: arrival(extra),
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

type Answer = Record<string, unknown> | { error: string };

async function renderArrival(given: Record<string, () => Answer>) {
  const variables: Record<string, unknown> = {};
  const answers: Record<string, () => Answer> = { Viewer: () => ({ viewer: VIEWER }), ...given };
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
  const client = new ApolloClient({ cache: new InMemoryCache({ typePolicies }), link });
  await render(
    <SafeAreaProvider initialMetrics={metrics}>
      <ApolloProvider client={client}>
        <ArrivalScreen bookingId="booking-1" onBack={jest.fn()} />
      </ApolloProvider>
    </SafeAreaProvider>,
  );
  return variables;
}

beforeEach(async () => {
  await i18n.changeLanguage('en');
});

it('lets the helper say they are on their way with an arrival time', async () => {
  const variables = await renderArrival({
    BookingArrival: () => ({ booking: booking('user-1', { canTap: true }) }),
    MarkOnMyWay: () => ({
      markOnMyWay: booking('user-1', {
        canTap: true,
        onMyWayAt: '2026-11-08T08:40:00Z',
        etaAt: '2026-11-08T09:00:00Z',
      }),
    }),
  });

  await screen.findByText(en.statusHelper.none);
  await fireEvent.press(screen.getByTestId('arrival-eta-20'));
  await fireEvent.press(screen.getByTestId('arrival-on-my-way'));

  expect(variables.MarkOnMyWay).toEqual({ bookingId: 'booking-1', etaMinutes: 20 });
  expect(screen.queryByTestId('arrival-on-my-way')).toBeNull();
});

it("shows the helper's error code in the user's language", async () => {
  await renderArrival({
    BookingArrival: () => ({ booking: booking('user-1', { canTap: true }) }),
    MarkArrived: () => ({ error: 'BOOKING_ACTION_NOT_ALLOWED' }),
  });
  await fireEvent.press(await screen.findByTestId('arrival-arrived'));
  await screen.findByText(catalogues.en.errors.BOOKING_ACTION_NOT_ALLOWED);
});

it("asks the poster to confirm a no-show with the 24-hour warning and the server's refund", async () => {
  const variables = await renderArrival({
    BookingArrival: () => ({
      booking: booking('helper-9', {
        canConfirmCame: true,
        canConfirmNoShow: true,
        noShowRefundCents: 3_360,
        noShowCreditCents: 500,
      }),
    }),
    ConfirmHelperArrival: () => ({
      confirmHelperArrival: {
        ...booking('helper-9', { noShowContestBy: '2026-11-09T10:00:00Z' }),
        status: 'CANCEL_REQUESTED',
      },
    }),
  });

  await fireEvent.press(await screen.findByTestId('arrival-came-no'));
  // Nothing is sent before the warning is confirmed.
  expect(variables.ConfirmHelperArrival).toBeUndefined();
  expect(
    screen.getByText(en.noShowWarning.replace('{amount}', formatMoney(3_360, 'en'))),
  ).toBeTruthy();
  expect(
    screen.getByText(en.noShowCredit.replace('{amount}', formatMoney(500, 'en'))),
  ).toBeTruthy();

  await fireEvent.press(screen.getByTestId('arrival-no-show-confirm'));

  expect(variables.ConfirmHelperArrival).toEqual({ bookingId: 'booking-1', came: false });
  await screen.findByTestId('arrival-no-show');
  expect(screen.queryByTestId('arrival-came-no')).toBeNull();
});

it('lets the helper answer "I was there" within the 24 hours', async () => {
  const reported = { noShowContestBy: '2026-11-09T10:00:00Z' };
  const variables = await renderArrival({
    BookingArrival: () => ({
      booking: booking('user-1', { ...reported, canContestNoShow: true }),
    }),
    ContestNoShow: () => ({
      contestNoShow: booking('user-1', {
        ...reported,
        noShowContestedAt: '2026-11-08T12:00:00Z',
        noShowAwaitingSupport: true,
      }),
    }),
  });

  await fireEvent.press(await screen.findByTestId('arrival-contest'));

  expect(variables.ContestNoShow).toEqual({ bookingId: 'booking-1' });
  await screen.findByText(en.contestedHelper);
});

it('lets the poster keep the booking or cancel for free after a late report', async () => {
  const variables = await renderArrival({
    BookingArrival: () => ({
      booking: booking('helper-9', {
        lateReportedAt: '2026-11-08T08:50:00Z',
        etaAt: '2026-11-08T09:40:00Z',
        canAnswerLate: true,
      }),
    }),
    RespondToLateArrival: () => ({
      respondToLateArrival: booking('helper-9', { lateReportedAt: '2026-11-08T08:50:00Z' }),
    }),
  });

  await screen.findByText(en.lateTitle);
  await fireEvent.press(screen.getByTestId('arrival-late-cancel'));

  expect(variables.RespondToLateArrival).toEqual({ bookingId: 'booking-1', keep: false });
});

it('keeps the withdrawal right in the early-start copy and sends the screen language', async () => {
  const pending = { earlyStartPending: true, canAnswerEarlyStart: true };
  const variables = await renderArrival({
    BookingArrival: () => ({
      booking: booking('helper-9', { ...pending, earlyStartWithdrawal: true }),
    }),
    RespondToEarlyStart: () => ({
      respondToEarlyStart: booking('helper-9', { earlyStartConfirmedAt: '2026-11-08T08:40:00Z' }),
    }),
  });

  await screen.findByText(en.earlyStartBodyWithdrawal);
  expect(screen.queryByText(en.earlyStartBody)).toBeNull();
  await fireEvent.press(screen.getByTestId('arrival-early-confirm'));

  expect(variables.RespondToEarlyStart).toEqual({
    bookingId: 'booking-1',
    confirm: true,
    locale: 'EN',
  });
});

it('never shows a side when the viewer is unknown', async () => {
  await renderArrival({
    BookingArrival: () => ({ booking: booking('user-1', { canTap: true }) }),
    Viewer: () => ({ viewer: null }),
  });
  await screen.findByText(en.loadError);
  expect(screen.queryByTestId('arrival-on-my-way')).toBeNull();
});

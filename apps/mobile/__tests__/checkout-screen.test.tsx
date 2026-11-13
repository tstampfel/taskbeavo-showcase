import { ApolloClient, ApolloLink, InMemoryCache } from '@apollo/client';
import { ApolloProvider } from '@apollo/client/react';
import { typePolicies } from '@taskbeavo/graphql';
import { catalogues, formatMoney, locales } from '@taskbeavo/i18n';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react-native';
import {
  AccessibilityInfo,
  ActivityIndicator,
  Modal,
  Pressable,
  ScrollView,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { Observable } from 'rxjs';
import { i18n } from '../src/i18n';
import type { PaymentSheet } from '../src/payments/stripe-config';
import { CheckoutScreen } from '../src/screens/checkout-screen';

// react-native's index requires each component the first time it is read. On a cold Jest cache
// the first render would transform these inside the first test's 5 s timeout, which a slow CI
// runner exceeded. Reading them here does that work at load time, where no timeout applies.
void [AccessibilityInfo, ActivityIndicator, Modal, Pressable, ScrollView, Text, TextInput, View];

const metrics = {
  frame: { x: 0, y: 0, width: 390, height: 844 },
  insets: { top: 47, left: 0, right: 0, bottom: 34 },
};
const NOW = new Date('2026-11-05T10:00:00Z');
const en = catalogues.en.checkout;

const offer = {
  __typename: 'Offer',
  id: 'offer-1',
  taskId: 'task-1',
  helperUserId: '7',
  helperIsTrader: false,
  helperBusinessName: null,
  helperBusinessOib: null,
  helperBusinessAddress: null,
  status: 'SENT',
  priceCents: 3_000,
  serviceFeeCents: 360,
  serviceFeeCreditCents: 0,
  serviceFeeScheduleVersion: '1',
  serviceFeePercent: 12,
  serviceFeeMinimumCents: 250,
  totalCents: 3_360,
  message: '',
  messageMasked: null,
  expiresAt: '2026-11-10T10:00:00Z',
  isMine: false,
  createdAt: '2026-11-03T10:00:00Z',
  updatedAt: '2026-11-03T10:00:00Z',
};

const task = {
  __typename: 'Task',
  id: 'task-1',
  status: 'OPEN',
  category: 'GARDEN',
  title: 'Mow lawn for Mama',
  description: '',
  approximateLat: 42.65,
  approximateLng: 18.09,
  serviceAreaName: 'Dubrovnik',
  windowStart: '2026-11-07T08:00:00Z',
  windowEnd: '2026-11-07T12:00:00Z',
  flexibleDate: false,
  budgetCents: 3_500,
  photos: [],
  isMine: true,
  createdAt: '2026-11-01T10:00:00Z',
  publishedAt: '2026-11-01T10:00:00Z',
  address: null,
  lat: null,
  lng: null,
  accessDetails: null,
  careContact: null,
  onSiteContact: null,
  textMasked: false,
  holdReasons: [],
};

const POLICY_TICK = 'I accept the cancellation policy of version 2026-11-c.';
const EARLY_TICK = 'I ask for the service to start before the withdrawal period ends.';
const TRADER_EARLY_TICK = 'I ask a registered business to start before my withdrawal period ends.';

function policy(consent = true) {
  return {
    __typename: 'LegalDocument',
    id: 'doc-1',
    kind: 'CANCELLATION',
    version: '2026-11-c',
    language: 'EN',
    effectiveAt: '2026-11-01T00:00:00Z',
    text: 'The full cancellation policy.',
    checkoutConsent: consent
      ? {
          __typename: 'CheckoutConsentTexts',
          privateHelper: {
            __typename: 'CheckoutConsentText',
            cancellationPolicy: POLICY_TICK,
            earlyStart: EARLY_TICK,
          },
          traderHelper: {
            __typename: 'CheckoutConsentText',
            cancellationPolicy: POLICY_TICK,
            earlyStart: TRADER_EARLY_TICK,
          },
        }
      : null,
  };
}

function booking(extra: Record<string, unknown> = {}) {
  return {
    __typename: 'Booking',
    id: 'booking-1',
    taskId: 'task-1',
    offerId: 'offer-1',
    helperUserId: '7',
    helperIsTrader: false,
    status: 'AWAITING_PAYMENT',
    amountCents: 3_000,
    feeCents: 360,
    creditCents: 0,
    totalCents: 3_360,
    slotStart: null,
    termsVersion: 'draft-1',
    cancellationVersion: '2026-11-c',
    checkoutExpiresAt: '2026-11-05T10:20:00Z',
    lastPaymentAttempt: null,
    checkoutFailureReason: null,
    createdAt: '2026-11-05T09:59:00Z',
    updatedAt: '2026-11-05T09:59:00Z',
    ...extra,
  };
}

const session = (paymentPending = false, extra: Record<string, unknown> = {}) => ({
  __typename: 'CheckoutSession',
  id: 'booking-1',
  clientSecret: 'pi_123_secret_456',
  paymentPending,
  booking: booking(extra),
});

type Answer = Record<string, unknown> | { error: string };

interface Api {
  answers: Record<string, () => Answer>;
  variables: Record<string, Record<string, unknown>>;
}

/** A client answering each operation by name; subscriptions stay open and silent. */
function client(api: Api) {
  const link = new ApolloLink(
    (operation) =>
      new Observable((observer) => {
        const name = operation.operationName ?? '';
        if (name === 'BookingUpdated') return;
        api.variables[name] = operation.variables;
        const answer = api.answers[name]?.();
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

function offerApi(answers: Api['answers'] = {}): Api {
  return {
    variables: {},
    answers: {
      TaskOffers: () => ({ taskOffers: [offer] }),
      Task: () => ({ task }),
      CheckoutPolicy: () => ({ currentLegalDocument: policy() }),
      AcceptOffer: () => ({ acceptOffer: session() }),
      Booking: () => ({ booking: booking() }),
      CheckoutSession: () => ({ checkoutSession: session() }),
      ...answers,
    },
  };
}

// The sheet's button confirms the card payment; it is not a second order button (R3-1).
const CARD_PAY = `Confirm card payment ${formatMoney(3_360, 'en')}`;

function fakeSheet(result: 'paid' | 'closed' | 'failed' = 'paid') {
  return { pay: jest.fn<ReturnType<PaymentSheet['pay']>, [string, string]>(async () => result) };
}

async function renderCheckout(
  api: Api,
  props: { offerId?: string; bookingId?: string; sheet?: PaymentSheet } = {},
) {
  const openLegal = jest.fn();
  const openFees = jest.fn();
  const onViewBooking = jest.fn();
  const onBackToOffers = jest.fn();
  await render(
    <SafeAreaProvider initialMetrics={metrics}>
      <ApolloProvider client={client(api)}>
        <CheckoutScreen
          taskId="task-1"
          offerId={props.bookingId ? undefined : (props.offerId ?? 'offer-1')}
          bookingId={props.bookingId}
          sheet={props.sheet ?? fakeSheet()}
          onBackToOffers={onBackToOffers}
          onViewBooking={onViewBooking}
          openLegal={openLegal}
          openFees={openFees}
          now={() => NOW}
        />
      </ApolloProvider>
    </SafeAreaProvider>,
  );
  return { onViewBooking, onBackToOffers, openLegal, openFees };
}

const payButton = () => screen.getByTestId('checkout-pay');
const isDisabled = () => payButton().props.accessibilityState?.disabled === true;

async function tickBoth() {
  await fireEvent.press(await screen.findByTestId('consent-cancellation-policy'));
  await fireEvent.press(screen.getByTestId('consent-early-start'));
}

describe('CheckoutScreen', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('en');
  });

  it('shows the price, the fee and the total from the server', async () => {
    await renderCheckout(offerApi());

    await screen.findByTestId('checkout-consent');
    expect(screen.getByText(formatMoney(3_000, 'en'))).toBeTruthy();
    expect(screen.getByText(formatMoney(360, 'en'))).toBeTruthy();
    expect(screen.getByText(formatMoney(3_360, 'en'))).toBeTruthy();
    expect(screen.getByText(en.helper.private)).toBeTruthy();
  });

  it('words both boxes from the cancellation policy and starts them unticked', async () => {
    await renderCheckout(offerApi());

    const policyBox = await screen.findByTestId('consent-cancellation-policy');
    const earlyBox = screen.getByTestId('consent-early-start');
    expect(screen.getByText(POLICY_TICK)).toBeTruthy();
    expect(screen.getByText(EARLY_TICK)).toBeTruthy();
    expect(policyBox.props.accessibilityState).toEqual({ checked: false });
    expect(earlyBox.props.accessibilityState).toEqual({ checked: false });
    expect(isDisabled()).toBe(true);

    await fireEvent.press(policyBox);
    expect(isDisabled()).toBe(true);
    await fireEvent.press(earlyBox);
    expect(isDisabled()).toBe(false);
  });

  it('opens the cancellation policy page in the in-app browser, in the checkout language', async () => {
    await i18n.changeLanguage('de');
    const { openLegal } = await renderCheckout(offerApi());

    await fireEvent.press(await screen.findByTestId('policy-page'));
    expect(openLegal).toHaveBeenCalledWith('cancellation', 'de');
  });

  it('links the price list next to the fee, before the pay button (BEAV-191)', async () => {
    await i18n.changeLanguage('hr');
    const { openFees } = await renderCheckout(offerApi());

    await fireEvent.press(await screen.findByTestId('checkout-fees'));
    expect(openFees).toHaveBeenCalledWith('hr');
    // ZZP čl. 74(3): the button names the obligation to pay.
    expect(screen.getByTestId('checkout-pay')).toHaveTextContent(/Naruči s obvezom plaćanja/);
  });

  it('shows the fee notice and the amount due right above the button, in Croatian', async () => {
    await i18n.changeLanguage('hr');
    await renderCheckout(offerApi());

    const notice = await screen.findByTestId('checkout-fee-notice');
    const plainText = (text: string) => text.replace(/[\u00a0\u202f]/g, ' ');
    expect(
      plainText(String(within(notice).getAllByText(/Cijena zadatka/)[0]?.props.children)),
    ).toBe(
      'Cijena zadatka 30,00 € (plaća se izvršitelju) + naknada za uslugu TaskBeavo 3,60 € (12 %, min. 2,50 €) = ukupno 33,60 €',
    );
    expect(plainText(String(screen.getByTestId('checkout-total-due').props.children))).toBe(
      'Ukupno za platiti: 33,60 €',
    );
    // hr: no amount on the order button; it is the only one on the screen.
    expect(plainText(String(payButton().props.accessibilityLabel ?? ''))).not.toMatch(/€/);
    expect(screen.getAllByTestId('checkout-pay')).toHaveLength(1);
  });

  it('opens the policy text from the legal document', async () => {
    await renderCheckout(offerApi());

    await fireEvent.press(await screen.findByTestId('policy-open'));
    expect(screen.getByText('The full cancellation policy.')).toBeTruthy();
    expect(screen.getByText('Version 2026-11-c')).toBeTruthy();
  });

  it('cannot be paid without the lawyer’s tick-box wording', async () => {
    await renderCheckout(
      offerApi({ CheckoutPolicy: () => ({ currentLegalDocument: policy(false) }) }),
    );

    expect(await screen.findByTestId('policy-unavailable')).toBeTruthy();
    expect(screen.queryByTestId('consent-early-start')).toBeNull();
    expect(isDisabled()).toBe(true);
  });

  it('accepts with the version shown, opens the sheet, then confirms until FUNDS_HELD', async () => {
    const api = offerApi();
    const sheet = fakeSheet('paid');
    await renderCheckout(api, { sheet });
    await tickBoth();

    await fireEvent.press(payButton());

    await waitFor(() => expect(sheet.pay).toHaveBeenCalledWith('pi_123_secret_456', CARD_PAY));
    expect(api.variables.AcceptOffer).toMatchObject({
      offerId: 'offer-1',
      expectedPriceCents: 3_000,
      expectedTotalCents: 3_360,
      expectedFeeScheduleVersion: '1',
      consent: {
        cancellationPolicyAccepted: true,
        earlyStartRequested: true,
        cancellationVersion: '2026-11-c',
        language: 'EN',
        variant: 'PRIVATE_HELPER',
      },
    });
    // Stripe's sheet succeeded, but only the webhook makes it held.
    expect(await screen.findByTestId('checkout-confirming')).toBeTruthy();
    expect(screen.queryByTestId('checkout-held')).toBeNull();
  });

  it('a registered business gets the trader wording, and that variant is sent', async () => {
    const api = offerApi({
      TaskOffers: () => ({
        taskOffers: [
          {
            ...offer,
            helperIsTrader: true,
            helperBusinessName: 'Vrtlarski obrt Horvat',
            helperBusinessOib: '00000000698',
            helperBusinessAddress: 'Obala Stjepana Radića 12, 20000 Dubrovnik, HR',
          },
        ],
      }),
    });
    await renderCheckout(api, { sheet: fakeSheet('paid') });

    expect(await screen.findByText(TRADER_EARLY_TICK)).toBeTruthy();
    // Next to the trader label: the business, its OIB and registered address (BEAV-192).
    expect(screen.getByText('Vrtlarski obrt Horvat · OIB 00000000698')).toBeTruthy();
    expect(
      screen.getByText(
        'Registered business address: Obala Stjepana Radića 12, 20000 Dubrovnik, HR',
      ),
    ).toBeTruthy();
    expect(screen.queryByText(EARLY_TICK)).toBeNull();
    await tickBoth();
    await fireEvent.press(payButton());

    await waitFor(() =>
      expect(api.variables.AcceptOffer).toMatchObject({ consent: { variant: 'TRADER_HELPER' } }),
    );
  });

  it('when the helper’s status changed, reloads the offer and unticks both boxes', async () => {
    let trader = false;
    const api = offerApi({
      TaskOffers: () => ({ taskOffers: [{ ...offer, helperIsTrader: trader }] }),
      AcceptOffer: () => {
        trader = true;
        return { error: 'CONSENT_WORDING_CHANGED' };
      },
    });
    const sheet = fakeSheet();
    await renderCheckout(api, { sheet });
    await tickBoth();

    await fireEvent.press(payButton());

    expect(await screen.findByText(TRADER_EARLY_TICK)).toBeTruthy();
    expect(screen.getByText(catalogues.en.errors.CONSENT_WORDING_CHANGED)).toBeTruthy();
    expect(screen.getByTestId('consent-early-start').props.accessibilityState).toEqual({
      checked: false,
    });
    expect(sheet.pay).not.toHaveBeenCalled();
  });

  it('shows the server’s refusal and charges nothing', async () => {
    const sheet = fakeSheet();
    await renderCheckout(offerApi({ AcceptOffer: () => ({ error: 'OFFER_CHANGED' }) }), {
      sheet,
    });
    await tickBoth();

    await fireEvent.press(payButton());

    expect(await screen.findByText(catalogues.en.errors.OFFER_CHANGED)).toBeTruthy();
    expect(sheet.pay).not.toHaveBeenCalled();
  });

  it('reloads the new total when the fee changed before the tap', async () => {
    // A new price list took effect: the server refuses €33.60, the offer now costs €34.50.
    let reloads = 0;
    const api = offerApi({
      TaskOffers: () => ({
        taskOffers: [
          reloads++ === 0
            ? offer
            : {
                ...offer,
                serviceFeeCents: 450,
                serviceFeeCreditCents: 0,
                serviceFeeScheduleVersion: '2',
                serviceFeePercent: 15,
                serviceFeeMinimumCents: 300,
                totalCents: 3_450,
              },
        ],
      }),
      AcceptOffer: () => ({ error: 'FEE_CHANGED' }),
    });
    const sheet = fakeSheet();
    await renderCheckout(api, { sheet });
    await tickBoth();

    await fireEvent.press(payButton());

    expect(await screen.findByText(catalogues.en.errors.FEE_CHANGED)).toBeTruthy();
    expect((await screen.findAllByText(/34\.50/)).length).toBeGreaterThan(0);
    expect(sheet.pay).not.toHaveBeenCalled();
    // Tapping again sends the list the reloaded notice quotes (review R3-2).
    await fireEvent.press(payButton());
    await waitFor(() =>
      expect(api.variables.AcceptOffer).toMatchObject({
        expectedTotalCents: 3_450,
        expectedFeeScheduleVersion: '2',
      }),
    );
  });

  it('stays on pay when the poster closes the sheet', async () => {
    await renderCheckout(offerApi(), { sheet: fakeSheet('closed') });
    await tickBoth();

    await fireEvent.press(payButton());

    await waitFor(() => expect(screen.getByTestId('checkout-expiry')).toBeTruthy());
    expect(screen.queryByTestId('checkout-confirming')).toBeNull();
    // Reopening the card payment is not a second order button.
    expect(payButton()).toHaveTextContent(/Confirm card payment/);
    expect(payButton()).not.toHaveTextContent(/Book and pay/);
  });

  it('shows the money held and the email once the booking is FUNDS_HELD', async () => {
    const { onViewBooking } = await renderCheckout(
      offerApi({ Booking: () => ({ booking: booking({ status: 'FUNDS_HELD' }) }) }),
      { bookingId: 'booking-1' },
    );

    expect(await screen.findByTestId('checkout-held')).toBeTruthy();
    expect(
      screen.getByText(en.held.title.replace('{amount}', formatMoney(3_360, 'en'))),
    ).toBeTruthy();
    expect(screen.getByText(new RegExp(en.held.email.slice(0, 30)))).toBeTruthy();
    await fireEvent.press(screen.getByText(en.held.viewBooking));
    expect(onViewBooking).toHaveBeenCalledWith('booking-1');
  });

  it('back on a credited booking, shows the schedule fee and the credit once (R1-3)', async () => {
    const credited = { feeCents: 0, creditCents: 360, totalCents: 3_000 };
    const api = offerApi({
      Booking: () => ({ booking: booking(credited) }),
      CheckoutSession: () => ({ checkoutSession: session(false, credited) }),
    });
    await renderCheckout(api, { bookingId: 'booking-1', sheet: fakeSheet('closed') });

    // The fee line is the schedule's 3.60, the credit line takes it off once, total 30.00.
    expect(
      await screen.findAllByText(new RegExp(formatMoney(360, 'en').replace('.', '\\.'))),
    ).toHaveLength(2);
    expect(screen.getAllByText(formatMoney(3_000, 'en'))).toHaveLength(2); // price and total
    expect(screen.queryByText(formatMoney(0, 'en'))).toBeNull();
  });

  it('after a decline, says why and retries on the same PaymentIntent', async () => {
    const declined = {
      lastPaymentAttempt: {
        __typename: 'PaymentAttempt',
        id: 'attempt-1',
        kind: 'DECLINED',
        declineCode: 'insufficient_funds',
        at: '2026-11-05T09:59:30Z',
      },
    };
    const api = offerApi({
      Booking: () => ({ booking: booking(declined) }),
      CheckoutSession: () => ({ checkoutSession: session(false, declined) }),
    });
    const sheet = fakeSheet('closed');
    await renderCheckout(api, { bookingId: 'booking-1', sheet });

    expect(await screen.findByTestId('checkout-declined')).toBeTruthy();
    expect(screen.getByText(en.declined.title)).toBeTruthy();
    expect(screen.getByText(new RegExp(en.decline.insufficient_funds))).toBeTruthy();

    await fireEvent.press(screen.getByText(en.declined.tryAgain));

    await waitFor(() => expect(sheet.pay).toHaveBeenCalledWith('pi_123_secret_456', CARD_PAY));
    expect(api.variables.CheckoutSession).toEqual({ bookingId: 'booking-1' });
  });

  it('a retry Stripe takes confirms, whatever the clocks say about the old decline', async () => {
    // The stored decline's server time is in the future of this device (skewed clock); only
    // a different attempt ID would mean a new decline (review R1-2).
    const declined = {
      lastPaymentAttempt: {
        __typename: 'PaymentAttempt',
        id: 'attempt-1',
        kind: 'DECLINED',
        declineCode: 'insufficient_funds',
        at: '2026-11-05T11:00:00Z',
      },
    };
    const api = offerApi({
      Booking: () => ({ booking: booking(declined) }),
      CheckoutSession: () => ({ checkoutSession: session(false, declined) }),
    });
    await renderCheckout(api, { bookingId: 'booking-1', sheet: fakeSheet('paid') });

    await fireEvent.press(await screen.findByText(en.declined.tryAgain));

    expect(await screen.findByTestId('checkout-confirming')).toBeTruthy();
  });

  it('back on a checkout Stripe already took, confirms instead of charging again', async () => {
    const sheet = fakeSheet();
    await renderCheckout(
      offerApi({ CheckoutSession: () => ({ checkoutSession: session(true) }) }),
      { bookingId: 'booking-1', sheet },
    );

    await fireEvent.press(await screen.findByTestId('checkout-pay'));

    expect(await screen.findByTestId('checkout-confirming')).toBeTruthy();
    expect(sheet.pay).not.toHaveBeenCalled();
  });

  it.each([
    ['CHECKOUT_EXPIRED', 'checkout-expired', en.expired.title],
    ['OFFER_WITHDRAWN', 'checkout-withdrawn', en.withdrawn.title],
    ['PAYMENT_NOT_STARTED', 'checkout-not-started', en.notStarted.title],
  ])('a checkout that ended with %s says so', async (reason, testID, title) => {
    const { onBackToOffers } = await renderCheckout(
      offerApi({
        Booking: () => ({
          booking: booking({ status: 'PAYMENT_FAILED', checkoutFailureReason: reason }),
        }),
      }),
      { bookingId: 'booking-1' },
    );

    expect(await screen.findByTestId(testID)).toBeTruthy();
    expect(screen.getByText(title)).toBeTruthy();
    await fireEvent.press(screen.getByRole('button'));
    expect(onBackToOffers).toHaveBeenCalledWith('task-1');
  });

  it('is expired once the time ran out, before the server ends it', async () => {
    await renderCheckout(
      offerApi({
        Booking: () => ({ booking: booking({ checkoutExpiresAt: '2026-11-05T09:59:00Z' }) }),
      }),
      { bookingId: 'booking-1' },
    );

    expect(await screen.findByTestId('checkout-expired')).toBeTruthy();
    expect(screen.queryByTestId('checkout-pay')).toBeNull();
  });

  it.each(locales)('never calls the held money "escrow" in %s', (locale) => {
    expect(JSON.stringify(catalogues[locale].checkout).toLowerCase()).not.toMatch(
      /escrow|treuhand|séquestre|sequestre|escrow/,
    );
  });
});

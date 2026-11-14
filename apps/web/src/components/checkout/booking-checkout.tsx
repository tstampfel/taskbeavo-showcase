'use client';

import { useApolloClient, useQuery } from '@apollo/client/react';
import { Elements, PaymentElement, useElements, useStripe } from '@stripe/react-stripe-js';
import { loadStripe, type Stripe, type StripePaymentElementOptions } from '@stripe/stripe-js';
import {
  BookingDocument,
  CheckoutSessionDocument,
  apiErrorCode,
  checkoutView,
  confirmDemoPayment,
  declineKey,
  demoPaymentsOn,
  type DemoCard,
  redirectOutcome,
  secondsUntilExpiry,
  submittedAfter,
  subscribeToBooking,
  type Submitted,
} from '@taskbeavo/graphql';
import { formatMoney, type Locale } from '@taskbeavo/i18n';
import { colors, fontFamily } from '@taskbeavo/ui-tokens';
import { useTranslations } from 'next-intl';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from '../../i18n/navigation';
import { Button, PinnedFoot } from '../ui';
import { BookingOutcome, DeclineNote, SecureLine, Summary } from './checkout-parts';
import styles from './checkout.module.css';
import { takeSecret } from './secret-handoff';

// While confirming, the booking is also refetched in case the live event is missed.
const CONFIRMING_POLL_MS = 5_000;
const COUNTDOWN_TICK_MS = 15_000;
// Stripe's redirect back from a bank's page carries these; they're dropped from the URL.
const STRIPE_RETURN_PARAMS = ['payment_intent', 'payment_intent_client_secret', 'redirect_status'];

/**
 * Stripe Link stays off: it would store a Stripe login on the device, which isn't strictly
 * necessary for the payment (BEAV-190). advancedFraudSignals stays at Stripe's default (on),
 * which is fine because Stripe.js only loads on this page (STRIPE_JS_MODULES in eslint.config.js).
 */
export const PAYMENT_ELEMENT_OPTIONS = {
  layout: 'tabs',
  wallets: { link: 'never' },
} as const satisfies StripePaymentElementOptions;

let stripePromise: Promise<Stripe | null> | null = null;

/**
 * Demo payments (BEAV-198): with NEXT_PUBLIC_DEMO_PAYMENTS=1 the API runs without Stripe, and
 * the demo card form below stands in for the Payment Element. Spelled out so Next.js inlines it.
 */
const DEMO_PAYMENTS = demoPaymentsOn(process.env.NEXT_PUBLIC_DEMO_PAYMENTS);
const DEMO_PAYMENTS_URL =
  process.env.NEXT_PUBLIC_DEMO_PAYMENTS_URL || 'http://localhost:8000/demo-stripe';

/** Stripe.js, loaded once; null when this deployment has no publishable key. */
function stripe(): Promise<Stripe | null> | null {
  const key = process.env.NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY;
  if (!key || !/^pk_(test|live)_\w+$/.test(key)) return null;
  stripePromise ??= loadStripe(key);
  return stripePromise;
}

/**
 * An open checkout and its outcome (mockups 6b–6f) with Stripe's Payment Element. The card
 * form only confirms the PaymentIntent the server made; "Confirming payment…" stays until the
 * server reports FUNDS_HELD (bookingUpdated, the poll, or the refetch when the tab is visible
 * again). A declined card stays on the form for another try on the same PaymentIntent.
 */
export function BookingCheckout({ bookingId, locale }: { bookingId: string; locale: Locale }) {
  const t = useTranslations('checkout');
  const router = useRouter();
  const booking = useQuery(BookingDocument, {
    variables: { id: bookingId },
    fetchPolicy: 'cache-and-network',
  });
  const { subscribeToMore, refetch, startPolling, stopPolling } = booking;
  const [submitted, setSubmitted] = useState<Submitted | null>(null);
  // Back from a bank's page that Stripe accepted: the submit marker waits for the booking.
  const [returned, setReturned] = useState(false);
  // Back from a bank's page that failed, before the webhook's decline arrives.
  const [returnDecline, setReturnDecline] = useState(false);
  const [now, setNow] = useState(() => new Date());
  const current = booking.data?.booking;
  // The last attempt the page has seen; a decline with another ID is newer (review R1-2).
  const lastAttemptId = useRef<string | null>(null);
  lastAttemptId.current = current?.lastPaymentAttempt?.id ?? null;
  // Stable, so the card form doesn't fetch the session again on every render.
  const markSubmitted = useCallback(() => {
    setReturnDecline(false);
    setSubmitted({ lastAttemptId: lastAttemptId.current });
  }, []);
  const refresh = useCallback(() => void refetch().catch(() => undefined), [refetch]);

  useEffect(() => subscribeToBooking(subscribeToMore, bookingId), [subscribeToMore, bookingId]);
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') void refetch().catch(() => undefined);
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [refetch]);
  useEffect(() => {
    // Back from a bank's page. Only succeeded or processing means Stripe has the payment;
    // failed stays on the form with the decline (review R1-2). The server says the rest.
    const url = new URL(window.location.href);
    if (!url.searchParams.has('payment_intent')) return;
    const outcome = redirectOutcome(url.searchParams.get('redirect_status'));
    if (outcome === 'submitted') setReturned(true);
    else setReturnDecline(true);
    STRIPE_RETURN_PARAMS.forEach((name) => url.searchParams.delete(name));
    window.history.replaceState(null, '', url);
  }, []);
  useEffect(() => {
    // The first booking after the return is what the page had "seen" at that submit.
    if (!returned || !current) return;
    setReturned(false);
    setSubmitted(submittedAfter(current));
  }, [returned, current]);

  const view = current && !returned ? checkoutView(current, { submitted }, now) : null;

  useEffect(() => {
    if (view?.kind !== 'confirming') return;
    startPolling(CONFIRMING_POLL_MS);
    return () => stopPolling();
  }, [view?.kind, startPolling, stopPolling]);
  useEffect(() => {
    if (view?.kind !== 'pay') return;
    const timer = setInterval(() => setNow(new Date()), COUNTDOWN_TICK_MS);
    return () => clearInterval(timer);
  }, [view?.kind]);

  if (!current || !view) {
    return booking.error ? (
      <section className={styles.outcome} data-testid="checkout-error">
        <p role="alert" className={styles.body}>
          {t('loadError')}
        </p>
        <Button onClick={() => void refetch().catch(() => undefined)}>{t('retry')}</Button>
      </section>
    ) : (
      <div className={styles.spinner} aria-label={t('title')} data-testid="checkout-loading" />
    );
  }

  if (view.kind !== 'pay') {
    return (
      <BookingOutcome
        booking={current}
        view={view}
        locale={locale}
        onBackToOffers={() => router.replace(`/tasks/${current.taskId}/offers`)}
        onViewBooking={() => router.replace(`/bookings/${current.id}`)}
      />
    );
  }

  const seconds = secondsUntilExpiry(current, now);
  return (
    <>
      {view.decline || returnDecline ? <DeclineNote decline={view.decline ?? 'generic'} /> : null}
      <Summary
        price={current.amountCents}
        // The booking's fee already has the credit off; the summary shows the schedule's fee
        // and the credit as its own line, so add it back (R1-3).
        fee={current.feeCents + (current.creditCents ?? 0)}
        credit={current.creditCents ?? 0}
        total={current.totalCents}
        locale={locale}
      />
      <SecureLine />
      {seconds !== null ? (
        <p className={`${styles.small} ${styles.muted}`} data-testid="checkout-expiry">
          {t('expiresIn', { minutes: Math.max(1, Math.ceil(seconds / 60)) })}
        </p>
      ) : null}
      <CardForm
        bookingId={current.id}
        locale={locale}
        totalCents={current.totalCents}
        retry={view.decline !== null || returnDecline}
        onSubmitted={markSubmitted}
        onClosed={refresh}
      />
    </>
  );
}

/** Fetches the client secret (handed off by acceptOffer, else checkoutSession) for Elements. */
function CardForm(props: {
  bookingId: string;
  locale: Locale;
  totalCents: number;
  retry: boolean;
  onSubmitted(): void;
  onClosed(): void;
}) {
  const t = useTranslations('checkout');
  const tAll = useTranslations();
  const client = useApolloClient();
  const [secret, setSecret] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const stripeLoader = DEMO_PAYMENTS ? null : stripe();
  const { bookingId, onSubmitted, onClosed } = props;

  useEffect(() => {
    if (!stripeLoader && !DEMO_PAYMENTS) return;
    const handedOff = takeSecret(bookingId);
    if (handedOff) {
      setSecret(handedOff);
      return;
    }
    let active = true;
    client
      .query({ query: CheckoutSessionDocument, variables: { bookingId }, fetchPolicy: 'no-cache' })
      .then(({ data }) => {
        if (!active || !data) return;
        if (data.checkoutSession.paymentPending) onSubmitted();
        else setSecret(data.checkoutSession.clientSecret);
      })
      .catch((caught: unknown) => {
        if (!active) return;
        setError(`errors.${apiErrorCode(caught) ?? 'PAYMENT_UNAVAILABLE'}`);
        // CHECKOUT_CLOSED: the booking says what happened.
        onClosed();
      });
    return () => {
      active = false;
    };
  }, [bookingId, client, stripeLoader, onSubmitted, onClosed]);

  if (!stripeLoader && !DEMO_PAYMENTS) {
    return (
      <p role="alert" className={styles.body} data-testid="checkout-not-configured">
        {t('notConfigured')}
      </p>
    );
  }
  if (error) {
    return (
      <p role="alert" className={`${styles.body} ${styles.error}`} data-testid="checkout-alert">
        {tAll(error)}
      </p>
    );
  }
  if (!secret) {
    return <div className={styles.spinner} aria-label={t('opening')} />;
  }
  if (!stripeLoader) {
    return <DemoPayForm {...props} secret={secret} />;
  }
  return (
    <Elements
      stripe={stripeLoader}
      options={{
        clientSecret: secret,
        locale: props.locale,
        appearance: {
          variables: { colorPrimary: colors.green, colorText: colors.ink, fontFamily },
        },
        fonts: [{ cssSrc: 'https://fonts.googleapis.com/css2?family=Manrope:wght@400;700' }],
      }}
    >
      <PayForm {...props} />
    </Elements>
  );
}

function PayForm({
  bookingId,
  locale,
  totalCents,
  retry,
  onSubmitted,
}: {
  bookingId: string;
  locale: Locale;
  totalCents: number;
  retry: boolean;
  onSubmitted(): void;
}) {
  const t = useTranslations('checkout');
  const stripeJs = useStripe();
  const elements = useElements();
  const [busy, setBusy] = useState(false);
  const [decline, setDecline] = useState<string | null>(null);

  async function pay() {
    if (!stripeJs || !elements) return;
    setBusy(true);
    setDecline(null);
    const { error } = await stripeJs.confirmPayment({
      elements,
      redirect: 'if_required',
      confirmParams: {
        return_url: `${window.location.origin}/${locale}/bookings/${bookingId}/checkout`,
      },
    });
    setBusy(false);
    if (!error) {
      onSubmitted();
      return;
    }
    // A field error is shown by the Payment Element itself; a decline gets our wording.
    if (error.type !== 'validation_error') setDecline(declineKey(error.decline_code ?? error.code));
  }

  return (
    <form
      className={styles.outcome}
      onSubmit={(event) => {
        event.preventDefault();
        void pay();
      }}
      data-testid="checkout-card-form"
    >
      {decline ? <DeclineNote decline={decline} /> : null}
      <PaymentElement options={PAYMENT_ELEMENT_OPTIONS} />
      <PinnedFoot testId="checkout-foot">
        <Button
          type="submit"
          fullWidth
          disabled={!stripeJs || busy}
          aria-busy={busy}
          data-testid="checkout-pay"
        >
          {retry || decline
            ? t('declined.tryAgain')
            : // Not the order button: the booking was placed with acceptOffer; this only
              // confirms the card payment (the bank may ask for 3-D Secure).
              t('cardPay', { amount: formatMoney(totalCents, locale) })}
        </Button>
      </PinnedFoot>
    </form>
  );
}

/**
 * The demo card form: THIS IS A DEMO STUB, shown only with demo payments. It confirms the
 * PaymentIntent at the API's stand-in for Stripe (no real card, no real money); the booking is
 * held only once the server reports FUNDS_HELD from the webhook, as with Stripe.
 */
export function DemoPayForm({
  secret,
  locale,
  totalCents,
  retry,
  onSubmitted,
}: {
  secret: string;
  locale: Locale;
  totalCents: number;
  retry: boolean;
  onSubmitted(): void;
}) {
  const t = useTranslations('checkout');
  const [card, setCard] = useState<DemoCard>('pays');
  const [busy, setBusy] = useState(false);
  const [decline, setDecline] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  async function pay() {
    setBusy(true);
    setDecline(null);
    setFailed(false);
    const outcome = await confirmDemoPayment(DEMO_PAYMENTS_URL, secret, card, fetch);
    setBusy(false);
    if (outcome === 'paid') onSubmitted();
    else if (outcome === 'declined') setDecline(declineKey('generic_decline'));
    else setFailed(true);
  }

  return (
    <form
      className={styles.outcome}
      onSubmit={(event) => {
        event.preventDefault();
        void pay();
      }}
      data-testid="checkout-demo-form"
    >
      {decline ? <DeclineNote decline={decline} /> : null}
      {/* Mockup 6g: the demo card in its own dashed card, so it never passes for a real form. */}
      <div className={styles.demoBox} data-testid="checkout-demo-card">
        <p className={styles.demoLabel} data-testid="checkout-demo-label">
          {t('demo.label')}
        </p>
        <p className={`${styles.small} ${styles.muted}`}>{t('demo.note')}</p>
        <fieldset className={styles.demoCards}>
          <legend className={styles.label}>{t('demo.card')}</legend>
          {(['pays', 'declines'] as const).map((choice) => (
            <label key={choice} className={styles.demoCard}>
              <input
                type="radio"
                name="demo-card"
                value={choice}
                checked={card === choice}
                onChange={() => setCard(choice)}
              />
              {t(`demo.${choice}`)}
            </label>
          ))}
        </fieldset>
      </div>
      {failed ? (
        <p role="alert" className={`${styles.body} ${styles.error}`}>
          {t('demo.failed')}
        </p>
      ) : null}
      <PinnedFoot testId="checkout-foot">
        <Button type="submit" fullWidth disabled={busy} aria-busy={busy} data-testid="checkout-pay">
          {retry || decline
            ? t('declined.tryAgain')
            : t('cardPay', { amount: formatMoney(totalCents, locale) })}
        </Button>
      </PinnedFoot>
    </form>
  );
}

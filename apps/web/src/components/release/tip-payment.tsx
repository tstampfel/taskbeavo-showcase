'use client';

import { Elements, PaymentElement, useElements, useStripe } from '@stripe/react-stripe-js';
import { loadStripe, type Stripe } from '@stripe/stripe-js';
import { confirmDemoPayment, demoPaymentsOn, type DemoCard } from '@taskbeavo/graphql';
import { formatMoney, type Locale } from '@taskbeavo/i18n';
import { colors, fontFamily } from '@taskbeavo/ui-tokens';
import { useTranslations } from 'next-intl';
import { useState } from 'react';
import { PAYMENT_ELEMENT_OPTIONS } from '../checkout/payment-element-options';
import checkoutStyles from '../checkout/checkout.module.css';
import { Button } from '../ui';

/**
 * The poster's tip card form (BEAV-118, mockup 7f): Stripe's Payment Element for the tip's
 * PaymentIntent, or the demo card with demo payments. The release page loads this module
 * with next/dynamic only once the poster pays a tip, so Stripe.js loads only then
 * (STRIPE_JS_MODULES in eslint.config.js, BEAV-190). `onPaid` means Stripe took the payment;
 * the tip counts once the server says so from the webhook (hard rule a).
 */
export interface TipCardFormProps {
  clientSecret: string;
  bookingId: string;
  amountCents: number;
  locale: Locale;
  onPaid(): void;
}

const DEMO_PAYMENTS = demoPaymentsOn(process.env.NEXT_PUBLIC_DEMO_PAYMENTS);
const DEMO_PAYMENTS_URL =
  process.env.NEXT_PUBLIC_DEMO_PAYMENTS_URL || 'http://localhost:8000/demo-stripe';

let stripePromise: Promise<Stripe | null> | null = null;

/** Stripe.js, loaded once; null when this deployment has no publishable key. */
function stripe(): Promise<Stripe | null> | null {
  const key = process.env.NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY;
  if (!key || !/^pk_(test|live)_\w+$/.test(key)) return null;
  stripePromise ??= loadStripe(key);
  return stripePromise;
}

export default function TipCardForm(props: TipCardFormProps) {
  const t = useTranslations('release.tip');
  if (DEMO_PAYMENTS) return <DemoTipForm {...props} />;
  const loader = stripe();
  if (!loader) {
    return (
      <p role="alert" className={checkoutStyles.body} data-testid="release-tip-not-configured">
        {t('notConfigured')}
      </p>
    );
  }
  return (
    <Elements
      stripe={loader}
      options={{
        clientSecret: props.clientSecret,
        locale: props.locale,
        appearance: {
          variables: { colorPrimary: colors.green, colorText: colors.ink, fontFamily },
        },
        fonts: [{ cssSrc: 'https://fonts.googleapis.com/css2?family=Manrope:wght@400;700' }],
      }}
    >
      <StripeTipForm {...props} />
    </Elements>
  );
}

function StripeTipForm({ bookingId, amountCents, locale, onPaid }: TipCardFormProps) {
  const t = useTranslations('release.tip');
  const stripeJs = useStripe();
  const elements = useElements();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  async function pay() {
    if (!stripeJs || !elements) return;
    setBusy(true);
    setFailed(false);
    const { error } = await stripeJs.confirmPayment({
      elements,
      redirect: 'if_required',
      confirmParams: {
        return_url: `${window.location.origin}/${locale}/bookings/${bookingId}/release`,
      },
    });
    setBusy(false);
    if (!error) onPaid();
    // A field error is shown by the Payment Element itself.
    else if (error.type !== 'validation_error') setFailed(true);
  }

  return (
    <form
      className={checkoutStyles.outcome}
      onSubmit={(event) => {
        event.preventDefault();
        void pay();
      }}
      data-testid="release-tip-card-form"
    >
      <PaymentElement options={PAYMENT_ELEMENT_OPTIONS} />
      {failed ? (
        <p role="alert" className={`${checkoutStyles.body} ${checkoutStyles.error}`}>
          {t('failed')}
        </p>
      ) : null}
      <Button
        type="submit"
        fullWidth
        disabled={!stripeJs || busy}
        aria-busy={busy}
        data-testid="release-tip-submit"
      >
        {t('pay', { amount: formatMoney(amountCents, locale) })}
      </Button>
    </form>
  );
}

/**
 * THIS IS A DEMO STUB, shown only with demo payments: it confirms the tip's PaymentIntent at
 * the API's stand-in for Stripe (no real card, no real money).
 */
export function DemoTipForm({ clientSecret, amountCents, locale, onPaid }: TipCardFormProps) {
  const t = useTranslations();
  const [card, setCard] = useState<DemoCard>('pays');
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<'declined' | 'failed' | null>(null);

  async function pay() {
    setBusy(true);
    setFailed(null);
    const outcome = await confirmDemoPayment(DEMO_PAYMENTS_URL, clientSecret, card, fetch);
    setBusy(false);
    if (outcome === 'paid') onPaid();
    else setFailed(outcome);
  }

  return (
    <form
      className={checkoutStyles.outcome}
      onSubmit={(event) => {
        event.preventDefault();
        void pay();
      }}
      data-testid="release-tip-demo-form"
    >
      <div className={checkoutStyles.demoBox}>
        <p className={checkoutStyles.demoLabel}>{t('checkout.demo.label')}</p>
        <p className={`${checkoutStyles.small} ${checkoutStyles.muted}`}>
          {t('checkout.demo.note')}
        </p>
        <fieldset className={checkoutStyles.demoCards}>
          <legend className={checkoutStyles.label}>{t('checkout.demo.card')}</legend>
          {(['pays', 'declines'] as const).map((choice) => (
            <label key={choice} className={checkoutStyles.demoCard}>
              <input
                type="radio"
                name="demo-tip-card"
                value={choice}
                checked={card === choice}
                onChange={() => setCard(choice)}
              />
              {t(`checkout.demo.${choice}`)}
            </label>
          ))}
        </fieldset>
      </div>
      {failed ? (
        <p role="alert" className={`${checkoutStyles.body} ${checkoutStyles.error}`}>
          {failed === 'declined' ? t('release.tip.failed') : t('checkout.demo.failed')}
        </p>
      ) : null}
      <Button
        type="submit"
        fullWidth
        disabled={busy}
        aria-busy={busy}
        data-testid="release-tip-submit"
      >
        {t('release.tip.pay', { amount: formatMoney(amountCents, locale) })}
      </Button>
    </form>
  );
}

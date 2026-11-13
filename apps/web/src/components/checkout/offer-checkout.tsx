'use client';

import { useApolloClient, useMutation, useQuery } from '@apollo/client/react';
import {
  AcceptOfferDocument,
  BookingDocument,
  CheckoutPolicyDocument,
  TaskDocument,
  TaskOffersDocument,
  UNTICKED,
  apiErrorCode,
  checkoutConsentInput,
  graphqlLocale,
  type ConsentTicks,
} from '@taskbeavo/graphql';
import type { Locale } from '@taskbeavo/i18n';
import { useTranslations } from 'next-intl';
import { useState } from 'react';
import { useRouter } from '../../i18n/navigation';
import { Button } from '../ui';
import { OfferCheckoutForm } from './checkout-parts';
import styles from './checkout.module.css';
import { handOffSecret } from './secret-handoff';

/**
 * Checkout of an offer not accepted yet (mockup 6a). Pay sends acceptOffer with the version
 * of the policy shown; the server creates the booking and the PaymentIntent, and the card
 * form opens on the booking's own checkout page.
 */
export function OfferCheckout({
  taskId,
  offerId,
  locale,
}: {
  taskId: string;
  offerId: string;
  locale: Locale;
}) {
  const t = useTranslations('checkout');
  const tAll = useTranslations();
  const router = useRouter();
  const client = useApolloClient();
  const [ticks, setTicks] = useState<ConsentTicks>(UNTICKED);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const offers = useQuery(TaskOffersDocument, { variables: { taskId } });
  const task = useQuery(TaskDocument, { variables: { id: taskId } }).data?.task ?? null;
  const policyQuery = useQuery(CheckoutPolicyDocument, {
    variables: { language: graphqlLocale(locale) },
    fetchPolicy: 'network-only',
  });
  const [acceptOffer] = useMutation(AcceptOfferDocument, { fetchPolicy: 'no-cache' });

  const offer = offers.data?.taskOffers.find((item) => item.id === offerId);
  const policy = policyQuery.data?.currentLegalDocument ?? null;

  if (offers.error || policyQuery.error) {
    return (
      <section className={styles.outcome} data-testid="checkout-error">
        <p role="alert" className={styles.body}>
          {t('loadError')}
        </p>
        <Button
          onClick={() => {
            void offers.refetch().catch(() => undefined);
            void policyQuery.refetch().catch(() => undefined);
          }}
        >
          {t('retry')}
        </Button>
      </section>
    );
  }
  if (!offers.data || policyQuery.loading) {
    return (
      <div className={styles.spinner} aria-label={t('title')} data-testid="checkout-loading" />
    );
  }
  if (!offer) {
    return (
      <p role="alert" className={styles.body} data-testid="checkout-error">
        {/* The offer list no longer has it: withdrawn and gone, or never the poster's. */}
        {tAll('errors.OFFER_NOT_FOUND')}
      </p>
    );
  }

  async function pay() {
    if (!policy || !offer) return;
    setBusy(true);
    setError(null);
    try {
      const { data } = await acceptOffer({
        variables: {
          offerId: offer.id,
          expectedPriceCents: offer.priceCents,
          expectedTotalCents: offer.totalCents,
          expectedFeeScheduleVersion: offer.serviceFeeScheduleVersion,
          consent: checkoutConsentInput(policy, ticks, 'WEB', offer.helperIsTrader),
        },
      });
      if (!data) throw new Error('No checkout session');
      const session = data.acceptOffer;
      client.writeQuery({
        query: BookingDocument,
        variables: { id: session.booking.id },
        data: { booking: session.booking },
      });
      handOffSecret(session.booking.id, session.clientSecret);
      router.replace(`/bookings/${session.booking.id}/checkout`);
    } catch (caught) {
      const code = apiErrorCode(caught) ?? 'PAYMENT_UNAVAILABLE';
      setError(`errors.${code}`);
      if (code === 'OFFER_CHANGED' || code === 'FEE_CHANGED') {
        // The price or the fee schedule changed: reload the offer to show the new total.
        void offers.refetch().catch(() => undefined);
      }
      if (code === 'CONSENT_WORDING_CHANGED') {
        // The helper's status changed: reload it and ask for both boxes in the new wording.
        setTicks(UNTICKED);
        void offers.refetch().catch(() => undefined);
      }
      setBusy(false);
    }
  }

  return (
    <OfferCheckoutForm
      locale={locale}
      taskTitle={task?.title ?? null}
      helperIsTrader={offer.helperIsTrader}
      trader={{
        helperBusinessName: offer.helperBusinessName,
        helperBusinessOib: offer.helperBusinessOib,
        helperBusinessAddress: offer.helperBusinessAddress,
      }}
      priceCents={offer.priceCents}
      feeCents={offer.serviceFeeCents}
      feePercent={offer.serviceFeePercent}
      feeMinimumCents={offer.serviceFeeMinimumCents}
      creditCents={offer.serviceFeeCreditCents}
      totalCents={offer.totalCents}
      policy={policy}
      ticks={ticks}
      onTicks={setTicks}
      busy={busy}
      error={error}
      onPay={() => void pay()}
    />
  );
}

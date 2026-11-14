'use client';

import { feesPath, legalPath } from '@taskbeavo/app-links';
import {
  canPay,
  consentTexts,
  type BookingFieldsFragment,
  type CheckoutPolicyQuery,
  type CheckoutView,
  type ConsentTicks,
} from '@taskbeavo/graphql';
import { formatMoney, type Locale } from '@taskbeavo/i18n';
import { useTranslations } from 'next-intl';
import { useState, type ReactNode } from 'react';
import { TraderIdentity, type TraderFields } from '../offers/trader-identity';
import { Avatar, Badge, Button, Card, LockIcon, Sheet } from '../ui';
import styles from './checkout.module.css';

export type Policy = NonNullable<CheckoutPolicyQuery['currentLegalDocument']>;

export function Summary({
  price,
  fee,
  credit = 0,
  total,
  locale,
  header,
}: {
  /** What is being booked, above the amounts (mockup 6a): the task and the helper. */
  header?: ReactNode;
  price: number;
  fee: number;
  /** The poster's TaskBeavo credit taken off the fee (BEAV-123): its own line. */
  credit?: number;
  total: number;
  locale: Locale;
}) {
  const t = useTranslations('checkout.summary');
  const tCheckout = useTranslations('checkout');
  const money = (cents: number) => formatMoney(cents, locale);
  return (
    <Card data-testid="checkout-summary">
      {header}
      <dl className={styles.summary}>
        <div className={styles.line}>
          <dt>{t('price')}</dt>
          <dd>{money(price)}</dd>
        </div>
        <div className={styles.line}>
          <dt>{t('fee')}</dt>
          <dd>{money(fee)}</dd>
        </div>
        {credit > 0 && (
          <div className={styles.line} data-testid="checkout-credit">
            <dt>{t('credit')}</dt>
            <dd>{`−${money(credit)}`}</dd>
          </div>
        )}
        <div className={`${styles.line} ${styles.total}`}>
          <dt>{t('total')}</dt>
          <dd>{money(total)}</dd>
        </div>
      </dl>
      <a
        className={`${styles.link} ${styles.pageLink}`}
        href={feesPath(locale)}
        target="_blank"
        rel="noopener"
        data-testid="checkout-fees"
      >
        {tCheckout('feesLink')}
      </a>
    </Card>
  );
}

export interface OfferCheckoutFormProps {
  locale: Locale;
  taskTitle: string | null;
  helperIsTrader: boolean;
  /** A trader helper's business name, OIB and registered address (null for a private one). */
  trader: Omit<TraderFields, 'helperIsTrader'>;
  priceCents: number;
  feeCents: number;
  /** The rule of the price list in force, for the fee notice: 12 and 250 today. */
  feePercent: number;
  feeMinimumCents: number;
  /** The poster's TaskBeavo credit off the fee; `totalCents` already includes it. */
  creditCents: number;
  totalCents: number;
  /** The CANCELLATION version in force; null when it couldn't be loaded. */
  policy: Policy | null;
  ticks: ConsentTicks;
  onTicks(ticks: ConsentTicks): void;
  busy: boolean;
  /** A catalogue key, such as errors.OFFER_CHANGED. */
  error: string | null;
  onPay(): void;
}

/**
 * Mockup 6a: the summary, how the payment works, and the two separate, unticked boxes in
 * the words of the CANCELLATION version in force (legal text, never the catalogues).
 */
export function OfferCheckoutForm(props: OfferCheckoutFormProps) {
  const t = useTranslations('checkout');
  const tAll = useTranslations();
  const [reading, setReading] = useState(false);
  const { policy, ticks, onTicks } = props;
  // The lawyer's wording for this helper type (user decision 2026-11-04, review R1-1).
  const consent = consentTexts(policy, props.helperIsTrader);
  return (
    <>
      <Summary
        header={
          <div className={styles.booking}>
            <div className={styles.bookingTop}>
              <Avatar size="sm" />
              <div className={styles.bookingText}>
                {props.taskTitle ? <p className={styles.label}>{props.taskTitle}</p> : null}
                <Badge tone={props.helperIsTrader ? 'green' : 'grey'}>
                  {tAll(props.helperIsTrader ? 'offers.trader.business' : 'offers.trader.private')}
                </Badge>
              </div>
            </div>
            {/* Rule e: a trader helper's business name, OIB and address, before the amounts. */}
            <TraderIdentity offer={{ ...props.trader, helperIsTrader: props.helperIsTrader }} />
          </div>
        }
        price={props.priceCents}
        fee={props.feeCents}
        credit={props.creditCents}
        total={props.totalCents}
        locale={props.locale}
      />
      <SecureLine />
      <Card variant="flat">
        <h2 className={styles.label}>{t('how.title')}</h2>
        <ol className={styles.steps}>
          <li>{t('how.held')}</li>
          <li>{t('how.request')}</li>
          <li>{t('how.release')}</li>
        </ol>
      </Card>
      <p className={`${styles.small} ${styles.muted}`}>
        {t(props.helperIsTrader ? 'helper.business' : 'helper.private')}
      </p>
      {policy && consent ? (
        <fieldset className={styles.consent} data-testid="checkout-consent">
          <legend>{t('consent.legend')}</legend>
          <label className={styles.checkRow}>
            <input
              type="checkbox"
              checked={ticks.cancellationPolicy}
              onChange={(event) => onTicks({ ...ticks, cancellationPolicy: event.target.checked })}
              data-testid="consent-cancellation-policy"
            />
            <span>{consent.cancellationPolicy}</span>
          </label>
          <label className={styles.checkRow}>
            <input
              type="checkbox"
              checked={ticks.earlyStart}
              onChange={(event) => onTicks({ ...ticks, earlyStart: event.target.checked })}
              data-testid="consent-early-start"
            />
            <span>{consent.earlyStart}</span>
          </label>
          <button
            type="button"
            className={styles.link}
            onClick={() => setReading(true)}
            data-testid="policy-open"
          >
            {t('policy.open')}
          </button>
          <a
            className={`${styles.link} ${styles.pageLink}`}
            href={legalPath('cancellation', props.locale)}
            target="_blank"
            rel="noopener"
            data-testid="policy-page"
          >
            {t('policy.fullPage')}
          </a>
          <Sheet
            open={reading}
            onClose={() => setReading(false)}
            title={t('policy.open')}
            closeLabel={t('policy.close')}
          >
            <p className={`${styles.small} ${styles.muted}`}>
              {t('policy.version', { version: policy.version })}
            </p>
            <div
              className={`${styles.body} ${styles.policyText}`}
              lang={policy.language.toLowerCase()}
            >
              {policy.text}
            </div>
          </Sheet>
        </fieldset>
      ) : (
        <p
          role="alert"
          className={`${styles.body} ${styles.error}`}
          data-testid="policy-unavailable"
        >
          {t('policy.unavailable')}
        </p>
      )}
      {props.error ? (
        <p role="alert" className={`${styles.body} ${styles.error}`} data-testid="checkout-alert">
          {tAll(props.error)}
        </p>
      ) : null}
      {/* Pinned to the bottom of the screen (mockup 6a): the fee notice, the amount due and the
          one order button stay together, so nothing comes between them. */}
      <div className={styles.foot} data-testid="checkout-foot">
        <FeeNotice {...props} />
        {/* The one order button of the flow (user decision after the fee-consent opinions):
            the statutory wording, and in hr, de and fr no amount on it. */}
        <Button
          fullWidth
          disabled={!canPay(policy, ticks) || props.busy}
          aria-busy={props.busy}
          onClick={props.onPay}
          data-testid="checkout-pay"
        >
          {t('pay', { amount: formatMoney(props.totalCents, props.locale) })}
        </Button>
      </div>
    </>
  );
}

/** "Payment protected · held via Stripe": the money is held by Stripe, never by TaskBeavo. */
export function SecureLine() {
  const t = useTranslations('checkout');
  return (
    <p
      className={`${styles.small} ${styles.green} ${styles.iconRow}`}
      data-testid="checkout-secure"
    >
      <LockIcon />
      <span>{t('secure')}</span>
    </p>
  );
}

/**
 * The fee notice and the amount due, directly above the order button: the price, who it is
 * paid to, the fee with its rule, and the total, all from the API (user decision after the
 * fee-consent opinions; DRAFT wording for the lawyer, REVIEW.md).
 */
function FeeNotice(props: OfferCheckoutFormProps) {
  const t = useTranslations('checkout');
  const money = (cents: number) => formatMoney(cents, props.locale);
  return (
    <div className={styles.feeNotice} data-testid="checkout-fee-notice">
      <p className={styles.small}>
        {t('feeNotice', {
          price: money(props.priceCents),
          fee: money(props.feeCents),
          percent: props.feePercent,
          minimum: money(props.feeMinimumCents),
          total: money(props.totalCents + props.creditCents),
        })}
      </p>
      {props.creditCents > 0 && (
        <p className={styles.small} data-testid="checkout-credit-notice">
          {t('creditNotice', { credit: money(props.creditCents) })}
        </p>
      )}
      <p className={styles.label} data-testid="checkout-total-due">
        {t('totalDue', { total: money(props.totalCents) })}
      </p>
    </div>
  );
}

export interface BookingOutcomeProps {
  booking: Pick<BookingFieldsFragment, 'id' | 'amountCents' | 'feeCents' | 'totalCents'>;
  view: Exclude<CheckoutView, { kind: 'pay' }>;
  locale: Locale;
  onBackToOffers(): void;
  onViewBooking(): void;
}

/** Mockups 6b–6f: everything after the card form. Only the server's FUNDS_HELD shows "held". */
export function BookingOutcome({
  booking,
  view,
  locale,
  onBackToOffers,
  onViewBooking,
}: BookingOutcomeProps) {
  const t = useTranslations('checkout');
  const total = formatMoney(booking.totalCents, locale);
  switch (view.kind) {
    case 'authenticating':
      return (
        <Outcome testId="checkout-authenticating" title={t('authenticating.title')} waiting>
          <p className={styles.body}>{t('authenticating.body', { amount: total })}</p>
          <p className={`${styles.small} ${styles.muted}`}>{t('authenticating.note')}</p>
        </Outcome>
      );
    case 'confirming':
      return (
        <Outcome testId="checkout-confirming" title={t('confirming.title')} waiting>
          <p className={styles.body}>{t('confirming.body')}</p>
        </Outcome>
      );
    case 'held':
      return (
        <Outcome testId="checkout-held" title={t('held.title', { amount: total })}>
          <p className={styles.body}>{t('held.notified')}</p>
          <p className={`${styles.small} ${styles.muted}`}>{t('held.release')}</p>
          <p className={`${styles.small} ${styles.muted}`}>{t('held.email')}</p>
          <Button onClick={onViewBooking}>{t('held.viewBooking')}</Button>
        </Outcome>
      );
    case 'withdrawn':
      return (
        <Outcome testId="checkout-withdrawn" title={t('withdrawn.title')}>
          <p className={styles.body}>{t('withdrawn.body')}</p>
          <Button variant="secondary" onClick={onBackToOffers}>
            {t('withdrawn.others')}
          </Button>
        </Outcome>
      );
    case 'notStarted':
      return (
        <Outcome testId="checkout-not-started" title={t('notStarted.title')}>
          <p className={styles.body}>{t('notStarted.body')}</p>
          <Button variant="secondary" onClick={onBackToOffers}>
            {t('back')}
          </Button>
        </Outcome>
      );
    case 'expired':
      return (
        <Outcome testId="checkout-expired" title={t('expired.title')}>
          <p className={styles.body}>{t('expired.body')}</p>
          <Button variant="secondary" onClick={onBackToOffers}>
            {t('back')}
          </Button>
        </Outcome>
      );
  }
}

function Outcome({
  testId,
  title,
  waiting = false,
  children,
}: {
  testId: string;
  title: string;
  waiting?: boolean;
  children: ReactNode;
}) {
  return (
    <section className={styles.outcome} data-testid={testId} aria-live="polite" aria-busy={waiting}>
      <h2 className={styles.h2}>{title}</h2>
      {waiting ? <div className={styles.spinner} aria-hidden="true" /> : null}
      {children}
    </section>
  );
}

/** Mockup 6d: the decline above the card form; the poster tries again on the same payment. */
export function DeclineNote({ decline }: { decline: string }) {
  const t = useTranslations('checkout');
  return (
    <Card className={styles.declined} data-testid="checkout-declined">
      <p role="alert" className={`${styles.label} ${styles.error}`}>
        {t('declined.title')}
      </p>
      <p className={styles.body}>
        {t(`decline.${decline}`)} {t('declined.nothingCharged')}
      </p>
    </Card>
  );
}

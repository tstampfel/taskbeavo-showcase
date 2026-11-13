'use client';

import type { BookingDetail, BookingLinks, BookingPlace, DetailCheckout } from '@taskbeavo/graphql';
import { formatDateTime, formatMoney, type Locale } from '@taskbeavo/i18n';
import { useTranslations } from 'next-intl';
import { Link } from '../../i18n/navigation';
import { TraderIdentity } from '../offers/trader-identity';
import { Badge, Card } from '../ui';
import styles from './booking.module.css';

export interface BookingDetailPanelProps {
  bookingId: string;
  view: BookingDetail;
  title: string;
  slotStart: unknown;
  /** What the poster pays, for the poster; what the helper gets, for the helper. */
  amountCents: number;
  locale: Locale;
}

/** The booking screens the detail page links into, by their path segment. */
const LINKS: readonly { link: keyof BookingLinks; segment: string; primary?: boolean }[] = [
  { link: 'release', segment: 'release', primary: true },
  { link: 'noAccess', segment: 'no-access', primary: true },
  { link: 'arrival', segment: 'arrival' },
  { link: 'cancel', segment: 'cancel' },
];

/**
 * The booking detail (BEAV-115, mockup 05) for the side in `view`: where the booking stands,
 * the place, who the poster contracts with and the links into the pages that act. The exact
 * address and access details show only when the server sent them (hard rule e).
 */
export function BookingDetailPanel({
  bookingId,
  view,
  title,
  slotStart,
  amountCents,
  locale,
}: BookingDetailPanelProps) {
  const t = useTranslations('bookingDetail');
  const tCheckout = useTranslations('checkout');
  const tTrader = useTranslations('offers.trader');
  const time = (at: unknown) => (typeof at === 'string' ? formatDateTime(at, locale, 'short') : '');
  const money = formatMoney(amountCents, locale);
  const poster = view.role === 'poster';
  const shownLinks = LINKS.filter(({ link }) => view.links[link]);

  return (
    <>
      <h2 className={styles.h2}>{title}</h2>
      <Badge
        tone={view.status === 'PAYMENT_REQUESTED' ? 'orange' : 'green'}
        data-testid="booking-status"
      >
        {t(`status.${view.status}`)}
      </Badge>
      {view.steps && (
        <ol className={styles.steps} aria-label={t('stepsLabel')} data-testid="booking-steps">
          {view.steps.map(({ step, state }) => (
            <li
              key={step}
              className={`${styles.step} ${state === 'todo' ? '' : styles[state]}`}
              aria-current={state === 'current' ? 'step' : undefined}
              data-testid={`booking-step-${step}-${state}`}
            >
              {t(`steps.${step}`)}
            </li>
          ))}
        </ol>
      )}

      <Card className={styles.section}>
        <p className={styles.body}>{t('startsAt', { time: time(slotStart) })}</p>
        <p className={styles.body} data-testid="booking-amount">
          {poster ? t('totalPoster', { total: money }) : t('amountHelper', { amount: money })}
        </p>
        {view.steps && view.status !== 'AWAITING_PAYMENT' && view.status !== 'CLOSED' && (
          <p className={`${styles.small} ${styles.muted}`}>
            {poster ? t('heldPoster', { total: money }) : t('heldHelper', { amount: money })}
          </p>
        )}
        {view.confirmationEmailed && (
          <p className={`${styles.small} ${styles.muted}`}>{tCheckout('held.email')}</p>
        )}
      </Card>

      {view.checkout && (
        <CheckoutNotice
          checkout={view.checkout}
          time={time}
          payHref={view.links.checkout ? `/bookings/${bookingId}/checkout` : null}
        />
      )}

      <PlaceCard place={view.place} />

      {view.trader && (
        <Card className={styles.section} data-testid="booking-helper">
          <h3 className={styles.h2}>{t('helperTitle')}</h3>
          {view.trader.helperIsTrader ? (
            <TraderIdentity offer={view.trader} />
          ) : (
            <p className={styles.body}>{tTrader('private')}</p>
          )}
        </Card>
      )}

      {shownLinks.length > 0 && (
        <nav className={styles.section} aria-label={t('links.title')}>
          <h3 className={styles.h2}>{t('links.title')}</h3>
          {shownLinks.map(({ link, segment, primary }) => (
            <Link
              key={link}
              href={`/bookings/${bookingId}/${segment}`}
              className={primary ? styles.primary : styles.action}
              data-testid={`booking-link-${segment}`}
            >
              {t(`links.${link}`)}
            </Link>
          ))}
        </nav>
      )}

      <p className={`${styles.small} ${styles.muted}`}>{t('safety')}</p>
    </>
  );
}

function CheckoutNotice({
  checkout,
  time,
  payHref,
}: {
  checkout: DetailCheckout;
  time(at: unknown): string;
  payHref: string | null;
}) {
  const t = useTranslations('bookingDetail.checkout');
  const tCheckout = useTranslations('checkout');
  return (
    <Card className={styles.section} data-testid="booking-checkout">
      {checkout.kind === 'pay' ? (
        <>
          {checkout.decline && (
            <p className={styles.body} role="alert">
              {tCheckout(`decline.${checkout.decline}`)}
            </p>
          )}
          {checkout.expiresAt && (
            <p className={styles.body}>{t('pay', { time: time(checkout.expiresAt) })}</p>
          )}
        </>
      ) : checkout.kind === 'failed' ? (
        <p className={styles.body}>{t(`failed.${checkout.reason}`)}</p>
      ) : (
        <p className={styles.body}>{t(checkout.kind)}</p>
      )}
      {payHref && (
        <Link href={payHref} className={styles.primary} data-testid="booking-pay">
          {t('goToPayment')}
        </Link>
      )}
    </Card>
  );
}

function PlaceCard({ place }: { place: BookingPlace }) {
  const t = useTranslations('bookingDetail.place');
  return (
    <Card className={styles.section} data-testid="booking-place">
      <h3 className={styles.h2}>{t('title')}</h3>
      {place.kind === 'exact' ? (
        <>
          <p className={styles.body} data-testid="booking-address">
            {place.address}
          </p>
          {place.onlyWhileBooked && (
            <p className={`${styles.small} ${styles.muted}`}>{t('whileBooked')}</p>
          )}
          {place.accessDetails ? (
            <p className={styles.body} data-testid="booking-access">
              {t('access', { details: place.accessDetails })}
            </p>
          ) : null}
          {place.contact && (
            <>
              <p className={styles.body}>
                {t('contact', { name: place.contact.name, area: place.contact.area })}
              </p>
              {place.contact.phone && (
                <a className={styles.link} href={`tel:${place.contact.phone}`}>
                  {t('phone', { phone: place.contact.phone })}
                </a>
              )}
            </>
          )}
        </>
      ) : (
        <>
          <p className={styles.body} data-testid="booking-area">
            {place.area ? t('area', { area: place.area }) : t('areaUnknown')}
          </p>
          {place.hint && <p className={`${styles.small} ${styles.muted}`}>{t(place.hint)}</p>}
        </>
      )}
    </Card>
  );
}

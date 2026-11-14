'use client';

import type { BookingDetail, BookingLinks, BookingPlace, DetailCheckout } from '@taskbeavo/graphql';
import { formatDateTime, formatMoney, type Locale } from '@taskbeavo/i18n';
import { useTranslations } from 'next-intl';
import { Link } from '../../i18n/navigation';
import { TraderIdentity } from '../offers/trader-identity';
import { Badge, Card, ClockIcon, InfoIcon, Note, PhoneIcon } from '../ui';
import styles from './booking.module.css';

type Contact = NonNullable<Extract<BookingPlace, { kind: 'exact' }>['contact']>;

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

  const requested = view.status === 'PAYMENT_REQUESTED';

  return (
    <>
      {/* "Payment requested" has its own card below (7b), so no badge repeats it. */}
      {!requested && (
        <Badge tone="green" data-testid="booking-status">
          {t(`status.${view.status}`)}
        </Badge>
      )}
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

      {/* 7b: where the payment stands comes first. */}
      {requested && (
        <Card className={styles.hero} data-testid="booking-requested">
          <span className={styles.heroIcon}>
            <ClockIcon size={44} />
          </span>
          <h3 className={styles.h2} data-testid="booking-status">
            {t('status.PAYMENT_REQUESTED')}
          </h3>
          <p className={`${styles.small} ${styles.muted}`}>
            {poster ? t('requestedPoster') : t('requestedHelper', { amount: money })}
          </p>
        </Card>
      )}

      <Card className={styles.summary} data-testid="booking-summary">
        <p className={styles.caps}>{t('startsAt', { time: time(slotStart) })}</p>
        <h2 className={styles.summaryTitle}>{title}</h2>
        <p className={styles.total} data-testid="booking-amount">
          <span>{poster ? t('amountLabelPoster') : t('amountLabelHelper')}</span>
          <span>{money}</span>
        </p>
        {/* Only while Stripe still holds it: not once it's released or paid out. */}
        {(view.status === 'FUNDS_HELD' || requested) && (
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
      {view.place.kind === 'exact' && view.place.contact && (
        <ContactCard contact={view.place.contact} />
      )}

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

      <Note icon={<InfoIcon size={18} />} data-testid="booking-safety">
        {t('safety')}
      </Note>
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
            <Card variant="flat" className={styles.access}>
              <p className={styles.caps}>{t('accessTitle')}</p>
              <p className={styles.body} data-testid="booking-access">
                {place.accessDetails}
              </p>
            </Card>
          ) : null}
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

/** First letters of the first two words, for the contact's decorative avatar. */
export function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((word) => word.charAt(0).toLocaleUpperCase())
    .join('');
}

/**
 * The on-site contact (mockups 7b, 18a). Only rendered from the exact place, which the server
 * sends only to the poster and to the helper of an active booking (hard rule e); the place
 * card's "Shown to you only while booked" covers it too.
 */
function ContactCard({ contact }: { contact: Contact }) {
  const t = useTranslations('bookingDetail.place');
  return (
    <Card className={styles.section} data-testid="booking-contact">
      <div className={styles.contactRow}>
        <span className={styles.contactAvatar} aria-hidden="true">
          {initials(contact.name)}
        </span>
        <p className={styles.strong}>{t('contact', { name: contact.name, area: contact.area })}</p>
      </div>
      {contact.phone && (
        <a className={styles.call} href={`tel:${contact.phone}`} data-testid="booking-call">
          <PhoneIcon size={18} />
          {t('phone', { phone: contact.phone })}
        </a>
      )}
    </Card>
  );
}

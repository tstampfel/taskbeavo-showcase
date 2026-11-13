import { rankingPath } from '@taskbeavo/app-links';
import {
  OFFER_SORTS,
  arrangeOffers,
  canAcceptOffer,
  type OfferSort,
  type TaskOffersQuery,
  type TaskQuery,
} from '@taskbeavo/graphql';
import { formatDate, formatMoney, type Locale } from '@taskbeavo/i18n';
import { useTranslations } from 'next-intl';
import type { ReactNode } from 'react';
import { Badge, Button, Card } from '../ui';
import styles from './offers.module.css';
import { TraderIdentity } from './trader-identity';

type Offer = TaskOffersQuery['taskOffers'][number];

export interface OffersListProps {
  locale: Locale;
  offers: readonly Offer[];
  task: Pick<NonNullable<TaskQuery['task']>, 'title' | 'windowStart' | 'budgetCents'> | null;
  sort: OfferSort;
  comparing: boolean;
  now: Date;
  onSort(sort: OfferSort): void;
  onCompare(comparing: boolean): void;
  onAccept(offerId: string): void;
}

/**
 * The poster's offers (mockups 4a and 4c): open offers in the chosen order with Accept, withdrawn
 * and expired ones greyed at the end, or the open ones side by side. Helper names and ratings
 * aren't in the API yet (BEAV-144), so cards show the trader label and a "new helper" state.
 * Each card names a trader helper's business, OIB and registered address, or says a private
 * helper isn't a trader, and how obligations are split (ZZP čl. 61(1), BEAV-192).
 * `message` is the helper's note with contact details already masked by the server. Prices
 * are the all-in total the poster pays (price plus service fee, from the API), with the
 * helper's price below it; /ranking explains the order (CRD art. 6a, BEAV-191).
 */
export function OffersList({
  locale,
  offers,
  task,
  sort,
  comparing,
  now,
  onSort,
  onCompare,
  onAccept,
}: OffersListProps) {
  const t = useTranslations('offers');
  const { open, closed } = arrangeOffers(offers, sort, now);
  const money = (cents: number) => formatMoney(cents, locale);
  const date = (value: unknown) => formatDate(String(value), locale);
  const trader = (offer: Offer) => t(offer.helperIsTrader ? 'trader.business' : 'trader.private');
  const accept = (offer: Offer, size: 'sm' | 'md') => (
    <Button
      size={size}
      disabled={!canAcceptOffer(offer, now)}
      aria-label={t('acceptLabel', { total: money(offer.totalCents) })}
      onClick={() => onAccept(offer.id)}
    >
      {t('accept')}
    </Button>
  );

  if (comparing) {
    return (
      <section data-testid="offers-compare" className={styles.main}>
        <h1 className={styles.h1}>{t('compareTitle', { count: open.length })}</h1>
        <Card className={styles.tableWrap}>
          <table className={styles.table}>
            {/* Each column is one offer, headed by its price (names come with BEAV-144). */}
            <thead>
              <tr>
                <th scope="col">{t('row.price')}</th>
                {open.map((offer) => (
                  <th key={offer.id} scope="col">
                    {money(offer.totalCents)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              <Row label={t('row.helperPrice')} offers={open} value={(o) => money(o.priceCents)} />
              <Row label={t('row.rating')} offers={open} value={() => t('ratingNone')} />
              <Row
                label={t('row.status')}
                offers={open}
                value={(o) => t(o.helperIsTrader ? 'statusShort.business' : 'statusShort.private')}
              />
              <Row
                label={t('row.business')}
                offers={open}
                value={(o) => <TraderIdentity offer={o} privateNotice />}
              />
              <Row label={t('row.expires')} offers={open} value={(o) => date(o.expiresAt)} />
              <tr>
                <td aria-hidden="true" />
                {open.map((offer) => (
                  <td key={offer.id}>{accept(offer, 'sm')}</td>
                ))}
              </tr>
            </tbody>
          </table>
        </Card>
        <RankingLink locale={locale} />
        <Button variant="secondary" onClick={() => onCompare(false)}>
          {t('backToList')}
        </Button>
      </section>
    );
  }

  return (
    <section data-testid="offers-content" className={styles.main}>
      <h1 className={styles.h1}>{t('count', { count: offers.length })}</h1>
      {task ? (
        <Card variant="flat">
          <p className={styles.label}>{task.title}</p>
          {task.windowStart ? (
            <p className={`${styles.small} ${styles.muted}`}>{date(task.windowStart)}</p>
          ) : null}
          {task.budgetCents !== null ? (
            <p className={`${styles.small} ${styles.muted}`}>
              {t('budget', { amount: money(task.budgetCents) })}
            </p>
          ) : null}
        </Card>
      ) : null}
      {offers.length === 0 ? (
        <p>{t('empty')}</p>
      ) : (
        <>
          <div className={styles.toolbar}>
            <fieldset className={styles.chips} aria-label={t('sortLabel')}>
              {OFFER_SORTS.map((key) => (
                <button
                  key={key}
                  type="button"
                  className={styles.chip}
                  aria-pressed={sort === key}
                  onClick={() => onSort(key)}
                >
                  {t(`sort.${key}`)}
                </button>
              ))}
            </fieldset>
            {open.length > 1 ? (
              <Button variant="ghost" size="sm" onClick={() => onCompare(true)}>
                {t('compare')}
              </Button>
            ) : null}
          </div>
          <ul className={styles.list}>
            {open.map((offer) => (
              <li key={offer.id}>
                <Card data-testid={`offer-card-${offer.id}`}>
                  <div className={styles.cardTop}>
                    <div className={styles.grow}>
                      <Badge>{trader(offer)}</Badge>
                      <TraderIdentity offer={offer} privateNotice />
                      <p className={`${styles.small} ${styles.muted}`}>{t('newHelper')}</p>
                      <p className={`${styles.small} ${styles.muted}`}>
                        {t('expires', { date: date(offer.expiresAt) })}
                      </p>
                    </div>
                    <div className={styles.priceBox}>
                      <p className={styles.price}>{money(offer.totalCents)}</p>
                      <p className={`${styles.small} ${styles.muted}`}>{t('totalNote')}</p>
                      <p className={`${styles.small} ${styles.muted}`}>
                        {t('helperPrice', { price: money(offer.priceCents) })}
                      </p>
                    </div>
                  </div>
                  {/* Absent while loading: the note is fetched, never restored from storage. */}
                  {offer.message ? (
                    <p className={`${styles.small} ${styles.muted} ${styles.quote}`}>
                      {`“${offer.message}”`}
                    </p>
                  ) : null}
                  {offer.status === 'NEEDS_UPDATE' ? (
                    <p className={styles.small}>{t('needsUpdate')}</p>
                  ) : null}
                  <p className={`${styles.small} ${styles.muted}`}>{t('obligations')}</p>
                  {accept(offer, 'sm')}
                </Card>
              </li>
            ))}
            {closed.map((offer) => (
              <li key={offer.id}>
                <Card data-testid={`offer-card-${offer.id}`} className={styles.closed}>
                  <div className={styles.grow}>
                    <p className={styles.label}>{trader(offer)}</p>
                    <p className={styles.small}>
                      {/* Past its expiry but not yet marked by the server: it reads as expired. */}
                      {t(offer.status === 'WITHDRAWN' ? 'closed.WITHDRAWN' : 'closed.EXPIRED')}
                    </p>
                  </div>
                  <p className={`${styles.h2} ${styles.muted}`}>{money(offer.totalCents)}</p>
                </Card>
              </li>
            ))}
          </ul>
        </>
      )}
      <RankingLink locale={locale} />
    </section>
  );
}

/**
 * Where the offers are shown, the ranking parameters are one tap away (CRD art. 6a(1)(a)). It
 * opens a new tab like the other legal links, and says so to screen readers (review R3-3).
 */
function RankingLink({ locale }: { locale: Locale }) {
  const t = useTranslations('offers');
  return (
    <a
      className={styles.link}
      href={rankingPath(locale)}
      target="_blank"
      rel="noopener"
      data-testid="offers-ranking"
    >
      {t('rankingLink')}
      <span className={styles.srOnly}> {t('newTab')}</span>
    </a>
  );
}

function Row({
  label,
  offers,
  value,
}: {
  label: string;
  offers: readonly Offer[];
  value(offer: Offer): ReactNode;
}) {
  return (
    <tr>
      <th scope="row">{label}</th>
      {offers.map((offer) => (
        <td key={offer.id}>{value(offer)}</td>
      ))}
    </tr>
  );
}

import type { OfferFieldsFragment } from '@taskbeavo/graphql';
import { useTranslations } from 'next-intl';
import styles from './offers.module.css';

export type TraderFields = Pick<
  OfferFieldsFragment,
  'helperIsTrader' | 'helperBusinessName' | 'helperBusinessOib' | 'helperBusinessAddress'
>;

/**
 * Who the poster contracts with, next to the trader label (hard rule e, BEAV-192): a trader
 * helper's business name, OIB and registered business address, and in-app chat as the rapid
 * means of contact (CRD Art. 6(1)(b)-(c), ZZP čl. 61(1)). For a private helper, with
 * `privateNotice`, the line that EU consumer rights don't cover the task. The API sends the
 * business fields only for a trader, so a private helper's details can never show here, and
 * the address only to the task's poster (often the obrt owner's home, decision 2026-11-07).
 */
export function TraderIdentity({
  offer,
  privateNotice = false,
}: {
  offer: TraderFields;
  privateNotice?: boolean;
}) {
  const t = useTranslations('offers.trader');
  if (!offer.helperIsTrader) {
    return privateNotice ? (
      <p className={`${styles.small} ${styles.muted}`} data-testid="private-notice">
        {t('privateNotice')}
      </p>
    ) : null;
  }
  const {
    helperBusinessName: name,
    helperBusinessOib: oib,
    helperBusinessAddress: address,
  } = offer;
  return (
    <div data-testid="trader-identity">
      {name && oib ? <p className={styles.small}>{t('identity', { name, oib })}</p> : null}
      {address ? (
        <p className={`${styles.small} ${styles.muted}`}>{t('address', { address })}</p>
      ) : null}
      <p className={`${styles.small} ${styles.muted}`}>{t('contact')}</p>
    </div>
  );
}

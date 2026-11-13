import { feesPath } from '@taskbeavo/app-links';
import type { Locale } from '@taskbeavo/i18n';
import { useTranslations } from 'next-intl';
import styles from '../legal/legal.module.css';

/**
 * /{locale}/ranking (BEAV-191, user decision 2026-11-07): how the poster's offers are ordered,
 * as the code orders them today. The API returns a task's offers newest first
 * (`Offer.Meta.ordering`, `offers.services.offers_on_task`); `arrangeOffers` in
 * packages/graphql/src/offers.ts puts the open ones first, newest first or by lowest price,
 * and the withdrawn and expired ones after them. Nothing else, and no payment, changes the
 * order. Update this text when the order changes (ratings, BEAV-144). DRAFT until the lawyer
 * approves it; the hr, de and fr text awaits a professional translation (REVIEW.md).
 */
export function RankingView({ locale }: { locale: Locale }) {
  const t = useTranslations('ranking');
  return (
    <main className={styles.main} data-testid="ranking">
      <article className={styles.article}>
        <p className={styles.note} role="note" data-testid="ranking-draft">
          <strong>{t('draftTitle')}</strong> <span>{t('draftBody')}</span>
        </p>
        <div className={styles.body} data-testid="ranking-body">
          <h1 className={styles.title}>{t('heading')}</h1>
          <p>{t('intro')}</p>
          <ul>
            <li>{t('open')}</li>
            <li>{t('newest')}</li>
            <li>{t('lowestPrice')}</li>
            <li>{t('nothingElse')}</li>
          </ul>
          <p data-testid="ranking-paid">
            <strong>{t('paidTitle')}</strong> {t('paid')}
          </p>
          <p>
            {t.rich('fees', {
              link: (chunks) => <a href={feesPath(locale)}>{chunks}</a>,
            })}
          </p>
        </div>
      </article>
    </main>
  );
}

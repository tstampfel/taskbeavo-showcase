'use client';

import { useQuery } from '@apollo/client/react';
import {
  TaskDocument,
  TaskOffersDocument,
  msUntilNextExpiry,
  subscribeToTaskOffers,
  type OfferSort,
} from '@taskbeavo/graphql';
import type { Locale } from '@taskbeavo/i18n';
import { useTranslations } from 'next-intl';
import { useEffect, useState } from 'react';
import { useRouter } from '../../i18n/navigation';
import { Button } from '../ui';
import { OffersList } from './offers-list';
import styles from './offers.module.css';

/**
 * The poster's offers on their task, kept live by the four offer subscriptions and refetched
 * when the socket reconnects and when the tab becomes visible again. Accept opens checkout.
 */
export function OffersView({ taskId, locale }: { taskId: string; locale: Locale }) {
  const t = useTranslations('offers');
  const router = useRouter();
  const [sort, setSort] = useState<OfferSort>('newest');
  const [comparing, setComparing] = useState(false);
  const offers = useQuery(TaskOffersDocument, {
    variables: { taskId },
    fetchPolicy: 'cache-and-network',
    notifyOnNetworkStatusChange: true,
  });
  const task = useQuery(TaskDocument, { variables: { id: taskId } }).data?.task ?? null;
  const { subscribeToMore, refetch } = offers;
  const list = offers.data?.taskOffers;
  // Bumped when the next open offer expires, so it moves to the closed ones on screen.
  const [expiryTick, setExpiryTick] = useState(0);

  useEffect(() => subscribeToTaskOffers(subscribeToMore, taskId), [subscribeToMore, taskId]);
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') void refetch().catch(() => undefined);
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [refetch]);

  useEffect(() => {
    const wait = list ? msUntilNextExpiry(list, new Date()) : null;
    if (wait === null) return;
    const timer = setTimeout(() => setExpiryTick((tick) => tick + 1), wait + EXPIRY_MARGIN_MS);
    return () => clearTimeout(timer);
  }, [list, expiryTick]);

  if (!list) {
    return (
      <section
        className={styles.main}
        aria-busy={!offers.error}
        data-testid={offers.error ? 'offers-error' : 'offers-loading'}
      >
        {offers.error && !offers.loading ? (
          <>
            <p role="alert">{t('loadError')}</p>
            <Button onClick={() => void refetch().catch(() => undefined)}>{t('retry')}</Button>
          </>
        ) : null}
      </section>
    );
  }

  return (
    <OffersList
      locale={locale}
      offers={list}
      task={task}
      sort={sort}
      comparing={comparing}
      now={new Date()}
      onSort={setSort}
      onCompare={setComparing}
      onAccept={(offerId) => router.push(`/tasks/${taskId}/offers/${offerId}/checkout`)}
    />
  );
}

// Past the expiry instant, so the re-render sees the offer as expired.
const EXPIRY_MARGIN_MS = 50;

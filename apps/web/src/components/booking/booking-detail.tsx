'use client';

import { NetworkStatus } from '@apollo/client';
import { useQuery } from '@apollo/client/react';
import {
  BookingDetailDocument,
  ViewerDocument,
  bookingDetailView,
  bookingRole,
  subscribeToBookingDetail,
} from '@taskbeavo/graphql';
import type { Locale } from '@taskbeavo/i18n';
import { useTranslations } from 'next-intl';
import { useEffect } from 'react';
import { Button } from '../ui';
import styles from './booking.module.css';
import { BookingDetailPanel } from './booking-detail-panel';

/**
 * The booking detail for both sides (mockup 05), from the server's booking: live through
 * bookingUpdated, refetched when the tab becomes visible again and when the socket
 * reconnects (src/apollo/client.ts), since missed events aren't replayed.
 */
export function BookingDetailView({ bookingId, locale }: { bookingId: string; locale: Locale }) {
  const t = useTranslations('bookingDetail');
  const booking = useQuery(BookingDetailDocument, {
    variables: { id: bookingId },
    fetchPolicy: 'cache-and-network',
  });
  const viewerQuery = useQuery(ViewerDocument);
  const { subscribeToMore, refetch } = booking;
  useEffect(
    () => subscribeToBookingDetail(subscribeToMore, bookingId),
    [subscribeToMore, bookingId],
  );
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') void refetch().catch(() => undefined);
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [refetch]);
  const current = booking.data?.booking;
  const viewer = viewerQuery.data?.viewer;

  const retry = (
    <div className={styles.section}>
      <p className={`${styles.body} ${styles.error}`} role="alert">
        {t('loadError')}
      </p>
      <Button
        variant="secondary"
        onClick={() => {
          void booking.refetch().catch(() => undefined);
          void viewerQuery.refetch().catch(() => undefined);
        }}
      >
        {t('retry')}
      </Button>
    </div>
  );
  if (!current || (!viewer && viewerQuery.loading)) {
    return booking.loading || viewerQuery.loading ? null : retry;
  }
  const view = bookingDetailView(current, bookingRole(current, viewer?.id ?? null), new Date(), {
    // A cached copy from an earlier screen may predate the end of the booking (hard rule e).
    fresh: booking.networkStatus !== NetworkStatus.loading,
  });
  // Never show one side's page to someone whose side isn't known.
  if (view.kind === 'unknownViewer') return retry;
  return (
    <BookingDetailPanel
      bookingId={bookingId}
      view={view}
      title={current.task.title}
      slotStart={current.slotStart}
      amountCents={view.role === 'poster' ? current.totalCents : current.amountCents}
      locale={locale}
    />
  );
}

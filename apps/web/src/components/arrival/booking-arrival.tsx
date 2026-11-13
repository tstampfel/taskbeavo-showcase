'use client';

import { useMutation, useQuery } from '@apollo/client/react';
import {
  BookingArrivalDocument,
  CancelAsHelperDocument,
  ConfirmHelperArrivalDocument,
  ContestNoShowDocument,
  MarkArrivedDocument,
  MarkOnMyWayDocument,
  ReportRunningLateDocument,
  RequestEarlyStartDocument,
  RespondToEarlyStartDocument,
  RespondToLateArrivalDocument,
  ViewerDocument,
  apiErrorCode,
  arrivalRole,
  arrivalView,
  graphqlLocale,
} from '@taskbeavo/graphql';
import type { Locale } from '@taskbeavo/i18n';
import { useTranslations } from 'next-intl';
import { Link } from '../../i18n/navigation';
import { Button } from '../ui';
import styles from './arrival.module.css';
import { ArrivalPanel } from './arrival-panel';

/** The day of the job for both sides (mockup 18), from the server's booking. */
export function BookingArrival({ bookingId, locale }: { bookingId: string; locale: Locale }) {
  const t = useTranslations('arrival');
  const tErrors = useTranslations('errors');
  const booking = useQuery(BookingArrivalDocument, {
    variables: { id: bookingId },
    fetchPolicy: 'cache-and-network',
  });
  const viewerQuery = useQuery(ViewerDocument);
  const [onMyWay, goingOut] = useMutation(MarkOnMyWayDocument);
  const [late, reportingLate] = useMutation(ReportRunningLateDocument);
  const [arrived, arriving] = useMutation(MarkArrivedDocument);
  const [cantCome, cancelling] = useMutation(CancelAsHelperDocument);
  const [answerLate, answeringLate] = useMutation(RespondToLateArrivalDocument);
  const [confirmCame, confirming] = useMutation(ConfirmHelperArrivalDocument);
  const [contest, contesting] = useMutation(ContestNoShowDocument);
  const [startEarly, askingEarly] = useMutation(RequestEarlyStartDocument);
  const [answerEarly, answeringEarly] = useMutation(RespondToEarlyStartDocument);
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
  const view = arrivalView(current, arrivalRole(current, viewer?.id ?? null));
  // Never show one side's screen to someone whose side isn't known.
  if (view.kind === 'unknownViewer') return retry;

  const mutations = [
    goingOut,
    reportingLate,
    arriving,
    cancelling,
    answeringLate,
    confirming,
    contesting,
    askingEarly,
    answeringEarly,
  ];
  const error = mutations.find((m) => m.error)?.error;
  const errorCode = error ? apiErrorCode(error) : undefined;
  const busy = mutations.some((m) => m.loading);
  const refresh = () => void booking.refetch().catch(() => undefined);
  const vars = { bookingId };

  return (
    <>
      <ArrivalPanel
        view={view}
        locale={locale}
        busy={busy}
        actions={{
          onMyWay: (etaMinutes) =>
            void onMyWay({ variables: { ...vars, etaMinutes } }).catch(refresh),
          onLate: (etaMinutes) => void late({ variables: { ...vars, etaMinutes } }).catch(refresh),
          onArrived: () => void arrived({ variables: vars }).catch(refresh),
          onCantCome: () =>
            void cantCome({ variables: { id: bookingId, reason: 'CANT_COME' } })
              .then(refresh)
              .catch(refresh),
          onStartEarly: () => void startEarly({ variables: vars }).catch(refresh),
          onContest: () => void contest({ variables: vars }).catch(refresh),
          onAnswerLate: (keep) => void answerLate({ variables: { ...vars, keep } }).catch(refresh),
          onConfirmCame: (came) =>
            void confirmCame({ variables: { ...vars, came } }).catch(refresh),
          onAnswerEarly: (confirm) =>
            void answerEarly({
              variables: { ...vars, confirm, locale: graphqlLocale(locale) },
            }).catch(refresh),
        }}
      />
      {errorCode && (
        <p className={`${styles.body} ${styles.error}`} role="alert">
          {tErrors.has(errorCode) ? tErrors(errorCode) : t('loadError')}
        </p>
      )}
      <Link href={`/bookings/${bookingId}`} className={styles.link}>
        {t('back')}
      </Link>
    </>
  );
}

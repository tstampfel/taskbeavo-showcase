'use client';

import * as Sentry from '@sentry/nextjs';
import { useEffect } from 'react';
import { ErrorView } from '../../components/error-view';

/** A render error below the locale layout: reported, then a translated retry screen. */
export default function LocaleError({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  useEffect(() => {
    Sentry.captureException(error);
  }, [error]);
  return <ErrorView onRetry={retry} />;
}

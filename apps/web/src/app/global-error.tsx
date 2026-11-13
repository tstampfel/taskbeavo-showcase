'use client';

import * as Sentry from '@sentry/nextjs';
import { catalogues } from '@taskbeavo/i18n';
import { colors, cssVariables } from '@taskbeavo/ui-tokens';
import { usePathname } from 'next/navigation';
import { useEffect } from 'react';
import { ErrorViewText } from '../components/error-view';
import { pathLocale } from '../i18n/path-locale';

/** An error in the root layout itself: reported, then a translated page of its own. */
export default function GlobalError({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  useEffect(() => {
    Sentry.captureException(error);
  }, [error]);
  const locale = pathLocale(usePathname());
  const text = catalogues[locale].errorBoundary;
  return (
    <html lang={locale}>
      <body
        style={{ ...cssVariables(), margin: 0, background: colors.offWhite, color: colors.ink }}
      >
        <ErrorViewText title={text.title} body={text.body} retry={text.retry} onRetry={retry} />
      </body>
    </html>
  );
}

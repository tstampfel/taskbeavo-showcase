'use client';

import { colors } from '@taskbeavo/ui-tokens';
import { useTranslations } from 'next-intl';
import { Button } from './ui';

/** The translated "something went wrong" screen of the error boundaries. */
export function ErrorView({ onRetry }: { onRetry: () => void }) {
  const t = useTranslations('errorBoundary');
  return <ErrorViewText title={t('title')} body={t('body')} retry={t('retry')} onRetry={onRetry} />;
}

/** The same screen with its text passed in, for global-error, which renders outside next-intl. */
export function ErrorViewText({
  title,
  body,
  retry,
  onRetry,
}: {
  title: string;
  body: string;
  retry: string;
  onRetry: () => void;
}) {
  return (
    <main
      role="alert"
      data-testid="error-view"
      style={{ maxWidth: 720, margin: '0 auto', padding: '64px 16px', boxSizing: 'border-box' }}
    >
      <h1 style={{ color: colors.green, fontSize: '2rem', margin: 0 }}>{title}</h1>
      <p>{body}</p>
      <Button onClick={onRetry}>{retry}</Button>
    </main>
  );
}

'use client';

import { useQuery } from '@apollo/client/react';
import { ApiVersionDocument } from '@taskbeavo/graphql';
import { useTranslations } from 'next-intl';

/** Sample query: shows that the browser reaches the API through Apollo. */
export function ApiStatus() {
  const t = useTranslations('apiStatus');
  const { data, error, loading } = useQuery(ApiVersionDocument);

  let text = t('connecting');
  if (data) text = t('connected', { version: data.apiVersion });
  else if (error && !loading) text = t('unavailable');

  return (
    <p data-testid="api-status" role="status" style={{ fontSize: '0.875rem' }}>
      {text}
    </p>
  );
}

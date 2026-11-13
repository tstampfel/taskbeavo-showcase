'use client';

import { useMutation } from '@apollo/client/react';
import { SetConsentDocument } from '@taskbeavo/graphql';
import { toApiLocale, type Locale } from '@taskbeavo/i18n';
import { useLocale } from 'next-intl';
import { useCallback, useState, useSyncExternalStore } from 'react';
import {
  consentSnapshot,
  readConsent,
  subscribeConsent,
  visitorId,
  writeConsent,
  type ConsentChoice,
} from './consent-cookie';

export interface AnalyticsConsent {
  /** The stored answer; null before the first answer, undefined until the browser is read. */
  choice: ConsentChoice | null | undefined;
  saving: boolean;
  failed: unknown;
  /** Logs the choice as a CONSENT row, then stores it; false when the server refused it. */
  choose(granted: boolean): Promise<boolean>;
}

/**
 * This browser's analytics choice (BEAV-83). A choice counts only once the server has logged
 * it: until then nothing is stored and analytics stays as it was.
 */
export function useAnalyticsConsent(): AnalyticsConsent {
  const choice = useSyncExternalStore(subscribeConsent, consentSnapshot, () => undefined);
  const locale = useLocale() as Locale;
  const [save, { loading }] = useMutation(SetConsentDocument);
  const [failed, setFailed] = useState<unknown>(null);

  const choose = useCallback(
    async (granted: boolean) => {
      setFailed(null);
      const visitor = visitorId(readConsent());
      try {
        const { data } = await save({
          variables: {
            purpose: 'ANALYTICS',
            granted,
            language: toApiLocale(locale),
            visitorId: visitor,
          },
        });
        writeConsent({
          analytics: granted,
          visitorId: visitor,
          version: data?.setConsent.version ?? null,
        });
        return true;
      } catch (error: unknown) {
        setFailed(error);
        return false;
      }
    },
    [locale, save],
  );

  return { choice, saving: loading, failed, choose };
}

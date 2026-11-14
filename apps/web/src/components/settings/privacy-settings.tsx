'use client';

import { useMutation, useQuery } from '@apollo/client/react';
import { legalPath } from '@taskbeavo/app-links';
import {
  ConsentsDocument,
  SetConsentDocument,
  ViewerDocument,
  type ConsentPurpose,
} from '@taskbeavo/graphql';
import { toApiLocale, type Locale } from '@taskbeavo/i18n';
import { useLocale, useTranslations } from 'next-intl';
import { useId, useState } from 'react';
import { useAnalyticsConsent } from '../../consent/use-analytics-consent';
import { errorMessage } from '../sign-in/error-message';
import { LinkRow, ListCard, SwitchRow } from '../ui';
import styles from './settings.module.css';

type Marketing = Extract<ConsentPurpose, 'MARKETING_EMAIL' | 'MARKETING_PUSH'>;
const MARKETING: { purpose: Marketing; label: 'marketingEmail' | 'marketingPush' }[] = [
  { purpose: 'MARKETING_EMAIL', label: 'marketingEmail' },
  { purpose: 'MARKETING_PUSH', label: 'marketingPush' },
];

/**
 * Settings → Privacy and data (BEAV-83, mockup 19 "Privacy & data"): analytics in this
 * browser, and news and offers by email and by push. All off until the user agrees; every
 * change is logged as a CONSENT row before it shows as saved. Booking and payment messages
 * can't be switched off, so they aren't here. The mockup's "price-suggestion statistics"
 * switch is left out: TaskBeavo never suggests prices (hard rule k). Laid out as mockup 19a:
 * the switches in a list card, the footnote under it, then the documents as list rows.
 */
export function PrivacySettings() {
  const t = useTranslations();
  const locale = useLocale() as Locale;
  const titleId = useId();
  // Signed out, Business details sends the visitor to sign-in; nothing is asked here.
  const viewer = useQuery(ViewerDocument).data?.viewer;
  const { data } = useQuery(ConsentsDocument, {
    fetchPolicy: 'cache-and-network',
    skip: !viewer,
  });
  const [save, { loading }] = useMutation(SetConsentDocument);
  const browser = useAnalyticsConsent();
  const [error, setError] = useState<string | null>(null);

  const granted = (purpose: Marketing) =>
    data?.consents.find((consent) => consent.purpose === purpose)?.granted ?? false;

  const setMarketing = (purpose: Marketing, value: boolean) => {
    setError(null);
    const current = data?.consents.find((consent) => consent.purpose === purpose);
    // The switch moves at once; a refusal rolls it back. __typename lets the cache file it.
    const optimistic = {
      __typename: 'ConsentState' as const,
      id: current?.id ?? purpose,
      purpose,
      granted: value,
      version: current?.version ?? null,
      givenAt: current?.givenAt ?? null,
    };
    save({
      variables: { purpose, granted: value, language: toApiLocale(locale) },
      optimisticResponse: { setConsent: optimistic },
    }).catch((caught: unknown) => setError(errorMessage(t, caught)));
  };
  const setAnalytics = (value: boolean) => {
    setError(null);
    void browser.choose(value);
  };
  const failure = error ?? (browser.failed ? errorMessage(t, browser.failed) : null);

  return (
    <section className={styles.section} aria-labelledby={titleId} data-testid="privacy-settings">
      <h2 id={titleId} className={styles.heading}>
        {t('privacySettings.title')}
      </h2>
      <ListCard data-testid="privacy-consents">
        <SwitchRow
          title={t('privacySettings.analytics')}
          subtitle={t('privacySettings.analyticsWeb')}
          checked={browser.choice?.analytics === true}
          disabled={browser.saving || browser.choice === undefined}
          onChange={setAnalytics}
          testId="privacy-analytics"
        />
        {MARKETING.map(({ purpose, label }) => (
          <SwitchRow
            key={purpose}
            title={t(`privacySettings.${label}`)}
            subtitle={t('privacySettings.marketingBody')}
            checked={granted(purpose)}
            disabled={loading || !data}
            onChange={(value) => setMarketing(purpose, value)}
            testId={`privacy-${purpose.toLowerCase().replace('_', '-')}`}
          />
        ))}
      </ListCard>
      {failure && (
        <p className={styles.error} role="alert">
          {failure}
        </p>
      )}
      <p className={styles.caption}>{t('privacySettings.footnote')}</p>
      <ListCard data-testid="privacy-documents">
        <LinkRow
          title={t('legal.documents.PRIVACY')}
          href={legalPath('privacy', locale)}
          testId="privacy-notice-link"
        />
        <LinkRow
          title={t('legal.documents.COOKIES')}
          href={legalPath('cookies', locale)}
          testId="privacy-cookies-link"
        />
      </ListCard>
    </section>
  );
}

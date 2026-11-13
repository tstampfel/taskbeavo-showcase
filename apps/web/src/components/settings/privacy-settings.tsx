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
import styles from '../consent/consent.module.css';
import { errorMessage } from '../sign-in/error-message';
import own from '../sign-in/sign-in.module.css';

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
 * switch is left out: TaskBeavo never suggests prices (hard rule k).
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
      id: purpose,
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
    <section className={own.panel} aria-labelledby={titleId} data-testid="privacy-settings">
      <h2 id={titleId} className={own.title}>
        {t('privacySettings.title')}
      </h2>
      <div className={styles.options}>
        <Toggle
          label={t('privacySettings.analytics')}
          description={t('privacySettings.analyticsWeb')}
          checked={browser.choice?.analytics === true}
          disabled={browser.saving || browser.choice === undefined}
          onChange={setAnalytics}
          testId="privacy-analytics"
        />
        {MARKETING.map(({ purpose, label }) => (
          <Toggle
            key={purpose}
            label={t(`privacySettings.${label}`)}
            description={t('privacySettings.marketingBody')}
            checked={granted(purpose)}
            disabled={loading || !data}
            onChange={(value) => setMarketing(purpose, value)}
            testId={`privacy-${purpose.toLowerCase().replace('_', '-')}`}
          />
        ))}
      </div>
      {failure && (
        <p className={own.error} role="alert">
          {failure}
        </p>
      )}
      <p className={own.muted}>{t('privacySettings.footnote')}</p>
      <a className={styles.link} href={legalPath('privacy', locale)}>
        {t('legal.documents.PRIVACY')}
      </a>
      <a className={styles.link} href={legalPath('cookies', locale)}>
        {t('legal.documents.COOKIES')}
      </a>
    </section>
  );
}

function Toggle({
  label,
  description,
  checked,
  disabled,
  onChange,
  testId,
}: {
  label: string;
  description: string;
  checked: boolean;
  disabled: boolean;
  onChange(value: boolean): void;
  testId: string;
}) {
  const id = useId();
  return (
    <label className={styles.option} htmlFor={id}>
      <span className={styles.optionText}>
        <strong>{label}</strong>
        <span>{description}</span>
      </span>
      <input
        id={id}
        type="checkbox"
        role="switch"
        className={styles.switch}
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        data-testid={testId}
      />
    </label>
  );
}

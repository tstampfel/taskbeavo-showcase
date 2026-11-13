'use client';

import { legalPath } from '@taskbeavo/app-links';
import type { Locale } from '@taskbeavo/i18n';
import { useLocale, useTranslations } from 'next-intl';
import { useEffect, useId, useState } from 'react';
import { analytics } from '../../consent/analytics';
import { OPEN_CONSENT_EVENT } from '../../consent/consent-cookie';
import { useAnalyticsConsent } from '../../consent/use-analytics-consent';
import { Button } from '../ui';
import styles from './consent.module.css';

/**
 * The cookie banner (BEAV-83, mockup 19 "Cookies on TaskBeavo"; plan, GDPR section
 * "Correction, objection and consent"). Shown until this browser answers, and again from
 * the footer's "Cookie settings". Reject sits next to Accept with the same weight; Settings
 * opens the analytics switch. Every answer is logged as a CONSENT row first; analytics
 * (PostHog) starts only after a logged yes, and stops on a later no.
 *
 * It sits in the page flow under the header rather than over the page, so it never hides
 * what a visitor (or a test) is looking at.
 */
export function ConsentBanner() {
  const { choice, saving, failed, choose } = useAnalyticsConsent();
  const [reopened, setReopened] = useState(false);

  // The one place analytics follows the stored answer, on every page.
  const granted = choice?.analytics === true;
  useEffect(() => {
    if (choice === undefined) return;
    void analytics.apply(granted);
  }, [choice, granted]);

  useEffect(() => {
    const reopen = () => setReopened(true);
    window.addEventListener(OPEN_CONSENT_EVENT, reopen);
    return () => window.removeEventListener(OPEN_CONSENT_EVENT, reopen);
  }, []);

  if (choice === undefined || (choice !== null && !reopened)) return null;
  return (
    <ConsentPanel
      // A reopened banner starts on the switch, showing the stored answer.
      key={reopened ? 'settings' : 'first'}
      initialDetails={reopened}
      initialAnalytics={granted}
      saving={saving}
      failed={Boolean(failed)}
      onChoose={(yes) =>
        void choose(yes).then((saved) => {
          if (saved) setReopened(false);
        })
      }
    />
  );
}

export interface ConsentPanelProps {
  initialDetails: boolean;
  initialAnalytics: boolean;
  saving: boolean;
  failed: boolean;
  onChoose(analytics: boolean): void;
}

export function ConsentPanel({
  initialDetails,
  initialAnalytics,
  saving,
  failed,
  onChoose,
}: ConsentPanelProps) {
  const t = useTranslations('consent');
  const locale = useLocale() as Locale;
  const titleId = useId();
  const switchId = useId();
  const [details, setDetails] = useState(initialDetails);
  const [analyticsOn, setAnalyticsOn] = useState(initialAnalytics);

  return (
    <section className={styles.banner} aria-labelledby={titleId} data-testid="consent-banner">
      <h2 id={titleId} className={styles.title}>
        {details ? t('settingsTitle') : t('title')}
      </h2>
      {details ? (
        <div className={styles.options}>
          <div className={styles.option}>
            <span className={styles.optionText}>
              <strong>{t('essentialTitle')}</strong>
              <span>{t('essentialBody')}</span>
            </span>
            <input
              type="checkbox"
              className={styles.switch}
              checked
              disabled
              aria-label={t('essentialTitle')}
            />
          </div>
          <label className={styles.option} htmlFor={switchId}>
            <span className={styles.optionText}>
              <strong>{t('analyticsTitle')}</strong>
              <span>{t('analyticsBody')}</span>
            </span>
            <input
              id={switchId}
              type="checkbox"
              role="switch"
              className={styles.switch}
              checked={analyticsOn}
              onChange={(event) => setAnalyticsOn(event.target.checked)}
              data-testid="consent-analytics"
            />
          </label>
        </div>
      ) : (
        <p className={styles.body}>{t('body')}</p>
      )}
      <a className={styles.link} href={legalPath('cookies', locale)}>
        {t('policyLink')}
      </a>
      {failed && (
        <p className={styles.error} role="alert">
          {t('saveFailed')}
        </p>
      )}
      <div className={styles.actions}>
        {details ? (
          <Button
            disabled={saving}
            onClick={() => onChoose(analyticsOn)}
            data-testid="consent-save"
          >
            {t('save')}
          </Button>
        ) : (
          <>
            <Button disabled={saving} onClick={() => onChoose(false)} data-testid="consent-reject">
              {t('reject')}
            </Button>
            <Button
              variant="ghost"
              disabled={saving}
              onClick={() => setDetails(true)}
              data-testid="consent-settings"
            >
              {t('settings')}
            </Button>
            <Button disabled={saving} onClick={() => onChoose(true)} data-testid="consent-accept">
              {t('accept')}
            </Button>
          </>
        )}
      </div>
    </section>
  );
}

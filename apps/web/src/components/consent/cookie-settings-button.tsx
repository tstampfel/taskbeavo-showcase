'use client';

import { useTranslations } from 'next-intl';
import { OPEN_CONSENT_EVENT } from '../../consent/consent-cookie';
import styles from './consent.module.css';

/** The footer's "Cookie settings": opens the banner again, on the analytics switch (BEAV-83). */
export function CookieSettingsButton() {
  const t = useTranslations('consent');
  return (
    <button
      type="button"
      className={styles.footerButton}
      onClick={() => window.dispatchEvent(new Event(OPEN_CONSENT_EVENT))}
      data-testid="footer-cookie-settings"
    >
      {t('settingsTitle')}
    </button>
  );
}

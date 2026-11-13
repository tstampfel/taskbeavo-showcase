import { geoliteAttributionOrder, type Locale } from '@taskbeavo/i18n';
import { useTranslations } from 'next-intl';
import type { ReactNode } from 'react';
import styles from './legal.module.css';

/**
 * Where the privacy notice's section on the new-sign-in email wants MaxMind's attribution. The
 * lawyer puts this marker in the PRIVACY text; without it the attribution follows the text.
 */
export const GEOLITE_MARKER = '<!-- geolite-attribution -->';

/** The privacy text before and after the marker; `after` is null when there is no marker. */
export function splitAtGeoliteMarker(text: string): { before: string; after: string | null } {
  const at = text.indexOf(GEOLITE_MARKER);
  if (at === -1) return { before: text, after: null };
  return { before: text.slice(0, at), after: text.slice(at + GEOLITE_MARKER.length) };
}

/**
 * MaxMind's attribution, as the GeoLite EULA's current example words it. The English original
 * always shows; in Croatian a Croatian sentence comes first, in German and French a translated
 * sentence follows the English one (BEAV-190). Shown on the imprint and the privacy notice.
 */
export function GeoliteAttribution({ locale }: { locale: Locale }) {
  const t = useTranslations('legal');
  const link = (chunks: ReactNode) => <a href="https://www.maxmind.com">{chunks}</a>;
  const english = (
    <span lang="en" key="en">
      {t.rich('geolite', { link })}
    </span>
  );
  // lang: inside the privacy text the surrounding language is the document's, not the page's.
  const local = (
    <span lang={locale} key="local">
      {t.rich('geoliteLocal', { link })}
    </span>
  );
  const parts = geoliteAttributionOrder(locale).map((part) =>
    part === 'english' ? english : local,
  );
  return (
    <p className={styles.attribution} data-testid="legal-geolite">
      {parts.flatMap((part, index) => (index > 0 ? [' ', part] : [part]))}
    </p>
  );
}

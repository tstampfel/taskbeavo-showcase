import type { Locale } from './index';

/**
 * The order of MaxMind's GeoLite attribution sentences (BEAV-190): the English original
 * (`legal.geolite`, the EULA's example) always shows; in Croatian the Croatian sentence
 * (`legal.geoliteLocal`) comes first, in German and French the translated sentence follows the
 * English. Web and mobile both render from this, so a change to the order lands on both.
 */
export function geoliteAttributionOrder(locale: Locale): ('english' | 'local')[] {
  if (locale === 'en') return ['english'];
  return locale === 'hr' ? ['local', 'english'] : ['english', 'local'];
}

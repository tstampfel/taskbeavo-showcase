/**
 * Hermes on iOS ships no Intl.PluralRules, which i18next-icu needs for every ICU `plural`;
 * without it the raw message source is shown. The polyfill only installs itself where the
 * runtime lacks it, with the plural rules of the app's four languages.
 */
import '@formatjs/intl-pluralrules/polyfill.js';
import '@formatjs/intl-pluralrules/locale-data/hr.js';
import '@formatjs/intl-pluralrules/locale-data/en.js';
import '@formatjs/intl-pluralrules/locale-data/de.js';
import '@formatjs/intl-pluralrules/locale-data/fr.js';

import countryNameTable from './country-names.json';
import type { Locale } from './index';

/**
 * Countries for "Country you live in" (sign-up) and the phone number's prefix. Posters live
 * anywhere, so every ISO 3166-1 alpha-2 country is offered. The names are CLDR's, in
 * `country-names.json` (scripts/generate-country-names.mjs), so they need no catalogue keys.
 */
export const countryCodes: readonly string[] = (
  'AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ ' +
  'BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM ' +
  'DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS ' +
  'GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN ' +
  'KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ ' +
  'MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM ' +
  'PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV ' +
  'SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI ' +
  'VN VU WF WS XK YE YT ZA ZM ZW'
).split(' ');

/** Where most users live: shown first in the country list. */
export const featuredCountryCodes: readonly string[] = ['HR', 'DE', 'AT', 'CH', 'FR', 'BE'];

/**
 * International dialling codes, to start the phone number field with the account country's
 * prefix. Only a convenience: the user can type any international number, and the API decides.
 */
const callingCodes: Record<string, string> = {
  AD: '376',
  AL: '355',
  AT: '43',
  AU: '61',
  BA: '387',
  BE: '32',
  BG: '359',
  BR: '55',
  BY: '375',
  CA: '1',
  CH: '41',
  CN: '86',
  CY: '357',
  CZ: '420',
  DE: '49',
  DK: '45',
  EE: '372',
  ES: '34',
  FI: '358',
  FR: '33',
  GB: '44',
  GR: '30',
  HR: '385',
  HU: '36',
  IE: '353',
  IL: '972',
  IN: '91',
  IS: '354',
  IT: '39',
  JP: '81',
  LI: '423',
  LT: '370',
  LU: '352',
  LV: '371',
  MC: '377',
  MD: '373',
  ME: '382',
  MK: '389',
  MT: '356',
  NL: '31',
  NO: '47',
  NZ: '64',
  PL: '48',
  PT: '351',
  RO: '40',
  RS: '381',
  RU: '7',
  SE: '46',
  SI: '386',
  SK: '421',
  SM: '378',
  TR: '90',
  UA: '380',
  US: '1',
  VA: '379',
  XK: '383',
  ZA: '27',
};

/** "+49 " for Germany, or "+" when the prefix isn't in the table. */
export function phonePrefix(country: string): string {
  const code = callingCodes[country.toUpperCase()];
  return code ? `+${code} ` : '+';
}

/** The country's flag emoji, built from its regional indicator letters. */
export function countryFlag(country: string): string {
  if (!/^[A-Z]{2}$/.test(country)) return '';
  return String.fromCodePoint(...[...country].map((letter) => 0x1f1e6 + letter.charCodeAt(0) - 65));
}

const countryNames: Record<Locale, Record<string, string | undefined>> = countryNameTable.names;

/** The Unicode CLDR version the bundled country names come from (Unicode License v3). */
export const countryNamesCldrVersion: string = countryNameTable.cldr;

/**
 * The country's name in `locale` ("Deutschland"), from the bundled CLDR table: Hermes, the
 * app's JavaScript engine, has no `Intl.DisplayNames`, and the list showed bare codes
 * (BEAV-223). A code missing from the table falls back to `Intl`, then to the code itself.
 */
export function countryName(country: string, locale: Locale): string {
  const named = countryNames[locale][country];
  if (named) return named;
  try {
    return new Intl.DisplayNames([locale], { type: 'region' }).of(country) ?? country;
  } catch {
    return country;
  }
}

/** Featured countries first, then the rest sorted by name in `locale`. */
export function sortedCountries(locale: Locale): { code: string; name: string }[] {
  const named = (code: string) => ({ code, name: countryName(code, locale) });
  const rest = countryCodes
    .filter((code) => !featuredCountryCodes.includes(code))
    .map(named)
    .sort((a, b) => a.name.localeCompare(b.name, locale));
  return [...featuredCountryCodes.map(named), ...rest];
}

/**
 * The country to pre-select at sign-up: the region of the first browser or phone language
 * that names one ("de-AT" is Austria), else Croatia.
 */
export function defaultCountry(languageTags: readonly string[]): string {
  for (const tag of languageTags) {
    const region = tag.split(/[-_]/)[1]?.toUpperCase();
    if (region && countryCodes.includes(region)) return region;
  }
  return 'HR';
}

/** `text` lower-cased in `locale` without its accents: "Österreich" → "osterreich". */
function searchKey(text: string, locale: Locale): string {
  return text.normalize('NFD').replace(/\p{M}/gu, '').toLocaleLowerCase(locale);
}

/**
 * Whether a typed search finds `country`: part of its name, with or without accents
 * ("osterreich", "cesk", "curacao"), or its exact code ("HR"). An empty search finds all.
 */
export function countryMatches(
  country: { code: string; name: string },
  query: string,
  locale: Locale,
): boolean {
  const needle = searchKey(query.trim(), locale);
  if (!needle) return true;
  return searchKey(country.name, locale).includes(needle) || country.code === needle.toUpperCase();
}

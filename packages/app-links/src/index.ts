/**
 * Which links open the mobile app. The app (+native-intent, app.config.ts), the web app (the
 * association files and the pages behind the links) and their tests all read this file, so a
 * path or ID rule changes in one place.
 *
 * It imports nothing: app.config.ts loads it, and Expo's config loader rejects the catalogue
 * JSON that @taskbeavo/i18n's index imports without import attributes.
 */

/**
 * Universal links (iOS) and App Links (Android) open the app for these hosts. Each must serve
 * both association files itself, with a 200 and no redirect (apps/web README, "App links").
 */
export const LINK_HOSTS = ['taskbeavo.com', 'www.taskbeavo.com'] as const;

/** The app's custom scheme (`scheme` in apps/mobile/app.config.ts). */
export const APP_SCHEME = 'taskbeavo';

/** The web's locale prefixes: @taskbeavo/i18n `locales` (a test keeps them equal). */
export const LINK_LOCALES = ['hr', 'en', 'de', 'fr'] as const;

/**
 * The top-level path sections the association files hand to the app. Web pages mirror the
 * app's paths under a locale (`/de/bookings/7`), the app without one (`/bookings/7`).
 * Push routes and email links from the backend (notifications/kinds.py) use the same paths.
 *
 * Every path under these sections opens the app, which only knows APP_ROUTES and sends anything
 * else home. A new web-only page under one of them (say `/de/bookings/7/receipt`) must also get
 * an app route, or an `exclude` entry in the association files.
 */
export const LINKED_SECTIONS = ['tasks', 'bookings', 'conversations', 'helper'] as const;

/** An ID in a link: a number or an opaque token as the API hands it out, never a path. */
export const LINK_ID = /^[A-Za-z0-9_-]{1,64}$/;

const ID = LINK_ID.source.slice(1, -1);

/**
 * Every screen a link may open. A link only ever opens a screen: anything that moves money
 * (release, payment, cancellation) still needs the signed-in user's tap and the server's checks.
 */
export const APP_ROUTES = [
  new RegExp(`^/tasks/${ID}$`),
  // The poster's offers on their task, and checkout for one offer (BEAV-102, BEAV-111).
  new RegExp(`^/tasks/${ID}/offers$`),
  new RegExp(`^/tasks/${ID}/offers/${ID}/checkout$`),
  new RegExp(`^/bookings/${ID}$`),
  // An open checkout or its outcome; a bank's 3D Secure redirect returns here on web.
  new RegExp(`^/bookings/${ID}/checkout$`),
  new RegExp(`^/bookings/${ID}/release$`),
  new RegExp(`^/bookings/${ID}/no-access$`),
  // The day of the job: On my way, arrival, the no-show questions, early start (BEAV-124).
  new RegExp(`^/bookings/${ID}/arrival$`),
  new RegExp(`^/conversations/${ID}$`),
  // The helper's payout account. Stripe's hosted onboarding also returns here (`return`) and
  // asks for a new link when one expired (`refresh`): backend STRIPE_CONNECT_*_URL.
  /^\/helper\/payouts$/,
  /^\/helper\/payouts\/return$/,
  /^\/helper\/payouts\/refresh$/,
] as const;

/** Whether an app path (no locale, query or trailing slash) is a screen a link may open. */
export function isAppRoute(path: string): boolean {
  return APP_ROUTES.some((route) => route.test(path));
}

/** `/tasks/`, `/de/tasks/`, ... for each section, without and with each locale. */
export function linkedPathPrefixes(): string[] {
  const localePrefixes = ['', ...LINK_LOCALES.map((locale) => `/${locale}`)];
  return localePrefixes.flatMap((prefix) =>
    LINKED_SECTIONS.map((section) => `${prefix}/${section}/`),
  );
}

/**
 * The app builds (apps/mobile/app.config.ts variants; its test checks the IDs) and the web
 * environment variable holding each Android build's signing certificate fingerprints.
 */
export const APP_BUILDS = [
  {
    variant: 'production',
    id: 'com.taskbeavo.app',
    fingerprintsVar: 'ANDROID_SHA256_CERT_FINGERPRINTS',
  },
  {
    variant: 'preview',
    id: 'com.taskbeavo.app.preview',
    fingerprintsVar: 'ANDROID_SHA256_CERT_FINGERPRINTS_PREVIEW',
  },
  {
    variant: 'development',
    id: 'com.taskbeavo.app.dev',
    fingerprintsVar: 'ANDROID_SHA256_CERT_FINGERPRINTS_DEVELOPMENT',
  },
] as const;

/** `applinks:` entries for the app's `ios.associatedDomains`. */
export function associatedDomains(): string[] {
  return LINK_HOSTS.map((host) => `applinks:${host}`);
}

/**
 * The verified App Links filter for the app's `android.intentFilters`. Android checks
 * /.well-known/assetlinks.json on each host at install (`autoVerify`); on Android 11 and older
 * one failing host turns verification off for the whole filter.
 */
export function androidIntentFilters() {
  return [
    {
      action: 'VIEW',
      autoVerify: true,
      data: LINK_HOSTS.flatMap((host) =>
        linkedPathPrefixes().map((pathPrefix) => ({ scheme: 'https', host, pathPrefix })),
      ),
      category: ['BROWSABLE', 'DEFAULT'],
    },
  ];
}

/**
 * The legal pages (BEAV-81): web pages at `/{locale}/{page}`, which the app opens in its in-app
 * browser. They are not app routes, so their links stay on the web. Every link to a legal page
 * (footer, sign-up, checkout, terms update, settings) is built with `legalPath` or `legalUrl`.
 */
export const LEGAL_PAGES = [
  'terms',
  'privacy',
  'helper-terms',
  'cancellation',
  'cookies',
  'imprint',
] as const;

export type LegalPage = (typeof LEGAL_PAGES)[number];

/** The API's LegalDocumentKind behind each page. */
export const LEGAL_PAGE_KINDS = {
  terms: 'TERMS',
  privacy: 'PRIVACY',
  'helper-terms': 'HELPER_TERMS',
  cancellation: 'CANCELLATION',
  cookies: 'COOKIES',
  imprint: 'IMPRINT',
} as const satisfies Record<LegalPage, string>;

/**
 * Where "close your account" on "We've updated our terms" leads (BEAV-188). Account deletion
 * (BEAV-153) and data export (BEAV-152) aren't built yet, so it is the Privacy notice, which
 * explains both rights. Point it at the settings screen here once they exist.
 */
export const CLOSE_ACCOUNT_PAGE = 'privacy' satisfies LegalPage;

/** `/de/terms`; with a version, that earlier (or scheduled) version: `/de/terms/2026-11`. */
export function legalPath(
  page: LegalPage,
  locale: (typeof LINK_LOCALES)[number],
  version?: string,
): string {
  return `/${locale}/${page}${version ? `/${encodeURIComponent(version)}` : ''}`;
}

/** The absolute URL the app opens in its in-app browser. */
export function legalUrl(page: LegalPage, locale: (typeof LINK_LOCALES)[number]): string {
  return `https://${LINK_HOSTS[0]}${legalPath(page, locale)}`;
}

/**
 * The public price list of TaskBeavo's service fee (BEAV-191, ZZP čl. 7(9)): `/{locale}/fees`,
 * earlier lists at `/{locale}/fees/archive`. Not a LEGAL_DOCUMENT: its numbers come from the
 * API's `serviceFeeSchedules`, the schedule checkout charges. Linked next to the legal pages
 * (footer, imprint, checkout); like them it is a web page the app opens in its in-app browser.
 */
export const FEES_PAGE = 'fees';

/** `/de/fees`, or `/de/fees/archive` for the earlier price lists. */
export function feesPath(locale: (typeof LINK_LOCALES)[number], archive = false): string {
  return `/${locale}/${FEES_PAGE}${archive ? '/archive' : ''}`;
}

/** The absolute URL of the fee page, for the app's in-app browser. */
export function feesUrl(locale: (typeof LINK_LOCALES)[number]): string {
  return `https://${LINK_HOSTS[0]}${feesPath(locale)}`;
}

/**
 * How the poster's offers are ordered (BEAV-191; ZZP / P2B ranking transparency):
 * `/{locale}/ranking`, linked from the price list and the footer. A web page the app opens in
 * its in-app browser, like the legal pages.
 */
export const RANKING_PAGE = 'ranking';

/** `/de/ranking`. */
export function rankingPath(locale: (typeof LINK_LOCALES)[number]): string {
  return `/${locale}/${RANKING_PAGE}`;
}

/** The absolute URL of the ranking page, for the app's in-app browser. */
export function rankingUrl(locale: (typeof LINK_LOCALES)[number]): string {
  return `https://${LINK_HOSTS[0]}${rankingPath(locale)}`;
}

/**
 * The machine-readable price list (NN 101/2026): the one charged now, or one version of it.
 * Not under a locale; the web serves it from the same API data as the page.
 */
export function priceListCsvPath(version?: string): string {
  return `/${FEES_PAGE}/pricelist${version ? `-${encodeURIComponent(version)}` : ''}.csv`;
}

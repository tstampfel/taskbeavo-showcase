import type { ConfigContext, ExpoConfig } from 'expo/config';
import type { ConfigPlugin } from 'expo/config-plugins';
import { withEntitlementsPlist } from 'expo/config-plugins';
import de from '@taskbeavo/i18n/locales/de.json' with { type: 'json' };
import en from '@taskbeavo/i18n/locales/en.json' with { type: 'json' };
import fr from '@taskbeavo/i18n/locales/fr.json' with { type: 'json' };
import hr from '@taskbeavo/i18n/locales/hr.json' with { type: 'json' };
import { androidIntentFilters, associatedDomains } from '@taskbeavo/app-links';
import { colors } from '@taskbeavo/ui-tokens';

/** Matches the build profiles in eas.json; each sets APP_ENV. */
const variants = {
  development: { name: 'TaskBeavo (Dev)', idSuffix: '.dev' },
  preview: { name: 'TaskBeavo (Preview)', idSuffix: '.preview' },
  production: { name: 'TaskBeavo', idSuffix: '' },
} as const;

type AppEnv = keyof typeof variants;

/**
 * The owner's Expo project `taskbeavo`, from `eas init`. It lives here because EAS CLI
 * can't write to a dynamic config.
 */
const easProjectId = '3ea41387-caed-438b-a103-a54f006a6ff9';

/**
 * Source maps and native debug files upload to Sentry's EU region during EAS Build, from the
 * SENTRY_AUTH_TOKEN, SENTRY_ORG and SENTRY_PROJECT EAS environment variables. Development
 * builds load JavaScript from Metro, so they skip the upload; preview and production builds
 * fail without a token rather than ship stacks no one can read.
 */
function sentryPlugin(env: AppEnv): [string, Record<string, unknown>] {
  return [
    '@sentry/react-native/expo',
    {
      url: 'https://de.sentry.io/',
      organization: process.env.SENTRY_ORG,
      project: process.env.SENTRY_PROJECT,
      disableAutoUpload: env === 'development',
    },
  ];
}

/**
 * The catalogues of packages/i18n, imported as JSON: its index can't be loaded by the config
 * loader, which needs the import attribute.
 */
const catalogues = { hr, en, de, fr };

/**
 * iOS permission prompts in one app language. The English text is also the Info.plist
 * default; Android's runtime prompts are worded by the system.
 */
function permissionTexts(locale: Locale) {
  return {
    NSCameraUsageDescription: message(locale, 'uploads.permission.cameraUsage'),
    NSPhotoLibraryUsageDescription: message(locale, 'uploads.permission.photoLibraryUsage'),
  };
}

type Locale = keyof typeof catalogues;

/**
 * One message by its full dotted key, so the catalogue check (packages/i18n) sees the key
 * used here. Throws on a missing key rather than build an app with an empty prompt.
 */
function message(locale: Locale, key: string): string {
  let node: unknown = catalogues[locale];
  for (const part of key.split('.')) {
    node =
      typeof node === 'object' && node !== null
        ? (node as Record<string, unknown>)[part]
        : undefined;
  }
  if (typeof node !== 'string') throw new Error(`Missing ${locale} message "${key}"`);
  return node;
}

/**
 * Google sign-in on iOS returns to the app on its reversed client ID scheme
 * (src/auth/providers/google.ts). The client ID is public and set per build as an EAS
 * environment variable; without it there is no Google button and no scheme.
 */
function googleIosSchemes(): string[] {
  const clientId = process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID;
  return clientId ? [clientId.split('.').reverse().join('.')] : [];
}

function appEnv(): AppEnv {
  const value = process.env.APP_ENV ?? 'development';
  if (!(value in variants)) {
    throw new Error(`Unknown APP_ENV "${value}"; expected ${Object.keys(variants).join(', ')}`);
  }
  return value as AppEnv;
}

/**
 * Set by scripts/run-ios.mjs when the Mac has no code signing identity. The development build
 * then drops SIGNING_ENTITLEMENTS so it can run on a simulator unsigned.
 */
export const UNSIGNED_SIMULATOR_ENV = 'TASKBEAVO_IOS_UNSIGNED_SIMULATOR';

/**
 * Entitlements that make `expo run:ios` sign even a simulator build
 * (@expo/cli run/ios/codeSigning/simulatorCodeSigning). Without a signing certificate it then
 * stops with "No code signing certificates are available to use" and a message about
 * physical iOS devices. Neither works in an unsigned build anyway: Sign in with Apple and
 * universal links both need the app signed by the team.
 */
export const SIGNING_ENTITLEMENTS = [
  'com.apple.developer.applesignin',
  'com.apple.developer.associated-domains',
] as const;

export function withoutSigningEntitlements<T extends Record<string, unknown>>(
  entitlements: T,
): Partial<T> {
  const rest: Record<string, unknown> = { ...entitlements };
  for (const key of SIGNING_ENTITLEMENTS) delete rest[key];
  return rest as Partial<T>;
}

/**
 * Removes the keys from an entitlements file an earlier signed prebuild wrote: prebuild without
 * --clean keeps the file's keys. The config itself leaves out everything that adds them.
 */
export const withUnsignedSimulator: ConfigPlugin = (config) =>
  withEntitlementsPlist(config, (mod) => {
    mod.modResults = withoutSigningEntitlements(mod.modResults);
    return mod;
  });

/**
 * `pnpm --filter @taskbeavo/mobile ios` on a Mac without a signing certificate builds the
 * development app unsigned (scripts/run-ios.mjs), without the entitlements that need signing.
 * A store, internal or EAS build must never lose them.
 */
function unsignedSimulator(env: AppEnv): boolean {
  if (process.env[UNSIGNED_SIMULATOR_ENV] !== '1') return false;
  // Only a local `expo run:ios` may drop them; EAS builds of every profile keep them.
  if (process.env.EAS_BUILD) {
    throw new Error(`${UNSIGNED_SIMULATOR_ENV} is only for local builds, not EAS Build`);
  }
  if (env !== 'development') {
    throw new Error(`${UNSIGNED_SIMULATOR_ENV} is only for development builds, not ${env}`);
  }
  return true;
}

export default ({ config }: ConfigContext): ExpoConfig => {
  const env = appEnv();
  const english = permissionTexts('en');
  const variant = variants[env];
  const unsigned = unsignedSimulator(env);
  const appId = `com.taskbeavo.app${variant.idSuffix}`;

  const appConfig: ExpoConfig = {
    ...config,
    name: variant.name,
    slug: 'taskbeavo',
    scheme: 'taskbeavo',
    platforms: ['ios', 'android'],
    version: '0.1.0',
    orientation: 'portrait',
    userInterfaceStyle: 'light',
    backgroundColor: colors.offWhite,
    runtimeVersion: { policy: 'fingerprint' },
    ios: {
      bundleIdentifier: appId,
      supportsTablet: false,
      // Lets iOS show the localized permission prompts below.
      infoPlist: { CFBundleAllowMixedLocalizations: true },
      // Merged with `scheme` by Expo. Setting infoPlist.CFBundleURLTypes instead would make
      // Expo skip `scheme`, and taskbeavo:// links would stop opening the app.
      scheme: googleIosSchemes(),
      // Sign in with Apple (src/auth/providers/apple.ts), required next to Google on iOS.
      usesAppleSignIn: !unsigned,
      // Universal links: the web app serves the matching apple-app-site-association.
      associatedDomains: unsigned ? [] : associatedDomains(),
    },
    locales: Object.fromEntries(
      (Object.keys(catalogues) as (keyof typeof catalogues)[]).map((locale) => [
        locale,
        { ios: permissionTexts(locale) },
      ]),
    ),
    android: {
      package: appId,
      intentFilters: androidIntentFilters(),
      // No Auto Backup to Google Drive: the persisted Apollo cache (src/apollo/cache-persist.ts)
      // holds other users' names, chat and booking addresses.
      allowBackup: false,
    },
    plugins: [
      'expo-router',
      'expo-dev-client',
      'expo-localization',
      'expo-font',
      ['expo-splash-screen', { backgroundColor: colors.offWhite }],
      // Session tokens (src/auth). No biometrics, so no Face ID usage string; Android
      // backups leave the store out, since restored keys can't decrypt it.
      ['expo-secure-store', { faceIDPermission: false }],
      // Photos for tasks and evidence (src/uploads). No video, so no microphone.
      [
        'expo-image-picker',
        {
          cameraPermission: english.NSCameraUsageDescription,
          photosPermission: english.NSPhotoLibraryUsageDescription,
          microphonePermission: false,
        },
      ],
      // Sign in with Apple: the entitlement for usesAppleSignIn.
      ...(unsigned ? [] : ['expo-apple-authentication']),
      // Google sign-in opens the system browser (expo-auth-session).
      'expo-web-browser',
      // Card, Apple Pay and Google Pay in Stripe's payment sheet (src/payments). The merchant ID
      // is registered with Apple and in the Stripe Dashboard.
      [
        '@stripe/stripe-react-native',
        { merchantIdentifier: 'merchant.com.taskbeavo', enableGooglePay: true },
      ],
      // stripe-react-native resolves the Stripe iOS SDK through Swift Package Manager, which
      // needs dynamic frameworks; with static linkage `pod install` fails (BEAV-111 R1-4).
      ['expo-build-properties', { ios: { useFrameworks: 'dynamic' } }],
      sentryPlugin(env),
    ],
    experiments: {
      typedRoutes: true,
    },
    updates: { url: `https://u.expo.dev/${easProjectId}` },
    extra: { appEnv: env, eas: { projectId: easProjectId } },
  };
  // Clears the entitlements an earlier signed prebuild left in ios/.
  return unsigned ? withUnsignedSimulator(appConfig) : appConfig;
};

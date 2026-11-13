import type { ConfigContext, ExpoConfig } from 'expo/config';
import { catalogues, locales } from '@taskbeavo/i18n';
import { APP_BUILDS } from '@taskbeavo/app-links';
import { IOSConfig } from 'expo/config-plugins';
import appConfig from '../app.config';

function configFor(appEnv?: string): ExpoConfig {
  const previous = process.env.APP_ENV;
  if (appEnv === undefined) delete process.env.APP_ENV;
  else process.env.APP_ENV = appEnv;
  try {
    return appConfig({ config: {} } as ConfigContext);
  } finally {
    if (previous === undefined) delete process.env.APP_ENV;
    else process.env.APP_ENV = previous;
  }
}

function pluginNames(config: ExpoConfig): unknown[] {
  return (config.plugins ?? []).map((plugin) => (Array.isArray(plugin) ? plugin[0] : plugin));
}

describe('app.config', () => {
  it('uses development builds through expo-dev-client and Expo Router', () => {
    const names = pluginNames(configFor());
    expect(names).toEqual(expect.arrayContaining(['expo-dev-client', 'expo-router']));
  });

  it('links iOS pods as dynamic frameworks, which the Stripe SDK needs', () => {
    const plugin = (configFor().plugins ?? []).find(
      (entry) => Array.isArray(entry) && entry[0] === 'expo-build-properties',
    );
    expect(plugin).toEqual(['expo-build-properties', { ios: { useFrameworks: 'dynamic' } }]);
  });

  it('signs in with Apple and opens Google in the system browser', () => {
    const config = configFor();
    expect(pluginNames(config)).toEqual(
      expect.arrayContaining(['expo-apple-authentication', 'expo-web-browser']),
    );
    expect(config.ios?.usesAppleSignIn).toBe(true);
  });

  it("registers the reversed Google iOS client ID as a URL scheme only when it's set", () => {
    const previous = process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID;
    try {
      delete process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID;
      expect(configFor().ios?.scheme).toEqual([]);
      process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID = '123-abc.apps.googleusercontent.com';
      const config = configFor();
      expect(config.ios?.scheme).toEqual(['com.googleusercontent.apps.123-abc']);
      // Expo writes `scheme` only while CFBundleURLTypes is left to it.
      expect(config.ios?.infoPlist?.CFBundleURLTypes).toBeUndefined();
      expect(config.scheme).toBe('taskbeavo');
    } finally {
      if (previous === undefined) delete process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID;
      else process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID = previous;
    }
  });

  it('keeps taskbeavo:// in the iOS Info.plist next to the Google scheme', () => {
    const previous = process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID;
    try {
      process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID = '123-abc.apps.googleusercontent.com';
      const config = configFor();
      // What prebuild writes, through Expo's own scheme step.
      const plist = IOSConfig.Scheme.setScheme(config, config.ios?.infoPlist ?? {});
      const schemes = (plist.CFBundleURLTypes ?? []).flatMap((type) => type.CFBundleURLSchemes);
      expect(schemes).toEqual(
        expect.arrayContaining(['taskbeavo', 'com.googleusercontent.apps.123-abc']),
      );
    } finally {
      if (previous === undefined) delete process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID;
      else process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID = previous;
    }
  });

  it('pins the native layer with the fingerprint runtime version policy', () => {
    expect(configFor().runtimeVersion).toEqual({ policy: 'fingerprint' });
  });

  it('defaults to the development variant', () => {
    const config = configFor();
    expect(config.ios?.bundleIdentifier).toBe('com.taskbeavo.app.dev');
    expect(config.android?.package).toBe('com.taskbeavo.app.dev');
    expect(config.name).toBe('TaskBeavo (Dev)');
  });

  it('gives preview its own identifiers so it installs next to production', () => {
    const config = configFor('preview');
    expect(config.ios?.bundleIdentifier).toBe('com.taskbeavo.app.preview');
    expect(config.android?.package).toBe('com.taskbeavo.app.preview');
  });

  it('uses the plain identifiers and name in production', () => {
    const config = configFor('production');
    expect(config.ios?.bundleIdentifier).toBe('com.taskbeavo.app');
    expect(config.android?.package).toBe('com.taskbeavo.app');
    expect(config.name).toBe('TaskBeavo');
  });

  it.each(['development', 'preview', 'production'])(
    'links %s to the EAS project and its update URL',
    (appEnv) => {
      const config = configFor(appEnv);
      expect(config.extra?.eas?.projectId).toBe('3ea41387-caed-438b-a103-a54f006a6ff9');
      expect(config.updates?.url).toBe('https://u.expo.dev/3ea41387-caed-438b-a103-a54f006a6ff9');
    },
  );

  it.each(['development', 'preview', 'production'])(
    "uploads %s source maps to Sentry's EU region without a token in the config",
    (appEnv) => {
      const config = configFor(appEnv);
      const plugin = (config.plugins ?? []).find(
        (entry) => Array.isArray(entry) && entry[0] === '@sentry/react-native/expo',
      ) as [string, Record<string, unknown>] | undefined;
      expect(plugin?.[1]).toMatchObject({
        url: 'https://de.sentry.io/',
        disableAutoUpload: appEnv === 'development',
      });
      expect(plugin?.[1]).not.toHaveProperty('authToken');
      expect(config.extra?.appEnv).toBe(appEnv);
    },
  );

  it('rejects an unknown APP_ENV instead of guessing', () => {
    expect(() => configFor('staging')).toThrow(/APP_ENV/);
  });

  it('registers the taskbeavo URL scheme and the brand splash colour', () => {
    const config = configFor();
    expect(config.scheme).toBe('taskbeavo');
    const splash = (config.plugins ?? []).find(
      (plugin) => Array.isArray(plugin) && plugin[0] === 'expo-splash-screen',
    );
    expect(splash).toEqual(['expo-splash-screen', { backgroundColor: '#F5F4EF' }]);
  });

  it('keeps the persisted Apollo cache out of Android Auto Backup', () => {
    expect(configFor('production').android?.allowBackup).toBe(false);
  });

  it('keeps session tokens in SecureStore without asking for Face ID', () => {
    const plugin = (configFor().plugins ?? []).find(
      (entry) => Array.isArray(entry) && entry[0] === 'expo-secure-store',
    );
    expect(plugin).toEqual(['expo-secure-store', { faceIDPermission: false }]);
  });

  it('adds the image picker with English permission texts and no microphone', () => {
    const plugin = (configFor().plugins ?? []).find(
      (entry) => Array.isArray(entry) && entry[0] === 'expo-image-picker',
    );
    const { cameraUsage, photoLibraryUsage } = catalogues.en.uploads.permission;
    expect(plugin).toEqual([
      'expo-image-picker',
      {
        cameraPermission: cameraUsage,
        photosPermission: photoLibraryUsage,
        microphonePermission: false,
      },
    ]);
  });

  it.each(locales)('translates the iOS camera and photo prompts into %s', (locale) => {
    const config = configFor();
    const { cameraUsage, photoLibraryUsage } = catalogues[locale].uploads.permission;
    expect(config.locales?.[locale]).toEqual({
      ios: {
        NSCameraUsageDescription: cameraUsage,
        NSPhotoLibraryUsageDescription: photoLibraryUsage,
      },
    });
    expect(config.ios?.infoPlist?.CFBundleAllowMixedLocalizations).toBe(true);
  });

  it.each(APP_BUILDS)(
    'builds the $variant app with the ID the association files list',
    ({ variant, id }) => {
      const config = configFor(variant);
      expect(config.ios?.bundleIdentifier).toBe(id);
      expect(config.android?.package).toBe(id);
    },
  );

  it.each(['development', 'preview', 'production'])(
    'opens taskbeavo.com links in the %s build on iOS (universal links)',
    (appEnv) => {
      expect(configFor(appEnv).ios?.associatedDomains).toEqual([
        'applinks:taskbeavo.com',
        'applinks:www.taskbeavo.com',
      ]);
    },
  );

  it('opens verified task, booking and conversation links on Android (App Links)', () => {
    const filters = configFor('production').android?.intentFilters ?? [];
    expect(filters).toHaveLength(1);
    const [filter] = filters;
    expect(filter).toMatchObject({
      action: 'VIEW',
      autoVerify: true,
      category: ['BROWSABLE', 'DEFAULT'],
    });
    const data = Array.isArray(filter?.data) ? filter.data : [];
    expect(data.every((entry) => entry.scheme === 'https')).toBe(true);
    expect(new Set(data.map((entry) => entry.host))).toEqual(
      new Set(['taskbeavo.com', 'www.taskbeavo.com']),
    );
    expect(data).toEqual(
      expect.arrayContaining([
        { scheme: 'https', host: 'taskbeavo.com', pathPrefix: '/bookings/' },
        { scheme: 'https', host: 'www.taskbeavo.com', pathPrefix: '/de/tasks/' },
      ]),
    );
    // Only the linked sections: the rest of the website stays in the browser.
    expect(data.some((entry) => entry.pathPrefix === '/' || entry.path === '/')).toBe(false);
  });

  it('adds the Stripe payment sheet with Apple Pay and Google Pay', () => {
    const plugin = (configFor().plugins ?? []).find(
      (entry) => Array.isArray(entry) && entry[0] === '@stripe/stripe-react-native',
    );
    expect(plugin).toEqual([
      '@stripe/stripe-react-native',
      { merchantIdentifier: 'merchant.com.taskbeavo', enableGooglePay: true },
    ]);
  });
});

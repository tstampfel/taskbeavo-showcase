import type { ConfigContext } from 'expo/config';
import type { ExportedConfig } from 'expo/config-plugins';
import appConfig, {
  SIGNING_ENTITLEMENTS,
  UNSIGNED_SIMULATOR_ENV,
  withoutSigningEntitlements,
} from '../app.config';

function configWith(env: Record<string, string | undefined>): ExportedConfig {
  const previous = Object.fromEntries(Object.keys(env).map((key) => [key, process.env[key]]));
  const assign = (values: Record<string, string | undefined>) => {
    for (const [key, value] of Object.entries(values)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  };
  assign(env);
  try {
    return appConfig({ config: {} } as ConfigContext);
  } finally {
    assign(previous);
  }
}

describe('unsigned simulator builds', () => {
  it('names the entitlements that make Expo CLI sign a simulator build', () => {
    expect(SIGNING_ENTITLEMENTS).toEqual([
      'com.apple.developer.applesignin',
      'com.apple.developer.associated-domains',
    ]);
  });

  it('drops only those entitlements', () => {
    expect(
      withoutSigningEntitlements({
        'com.apple.developer.applesignin': ['Default'],
        'com.apple.developer.associated-domains': ['applinks:taskbeavo.com'],
        'com.apple.developer.in-app-payments': ['merchant.com.taskbeavo'],
      }),
    ).toEqual({ 'com.apple.developer.in-app-payments': ['merchant.com.taskbeavo'] });
  });

  it('keeps every entitlement and adds no entitlements mod by default', () => {
    const config = configWith({ [UNSIGNED_SIMULATOR_ENV]: undefined, APP_ENV: undefined });
    expect(config.ios?.usesAppleSignIn).toBe(true);
    expect(config.mods).toBeUndefined();
  });

  it('leaves out Sign in with Apple and universal links in an unsigned development build', () => {
    const config = configWith({
      [UNSIGNED_SIMULATOR_ENV]: '1',
      APP_ENV: 'development',
      EAS_BUILD: undefined,
    });
    expect(config.ios?.usesAppleSignIn).toBe(false);
    expect(config.ios?.associatedDomains).toEqual([]);
    expect(config.plugins).not.toContain('expo-apple-authentication');
    expect(typeof config.mods?.ios?.entitlements).toBe('function');
  });

  it.each(['development', 'preview', 'production'])(
    'refuses the variable on an EAS %s build',
    (appEnv) => {
      expect(() =>
        configWith({ [UNSIGNED_SIMULATOR_ENV]: '1', APP_ENV: appEnv, EAS_BUILD: 'true' }),
      ).toThrow(UNSIGNED_SIMULATOR_ENV);
    },
  );

  it.each(['preview', 'production'])('refuses an unsigned %s build', (appEnv) => {
    expect(() => configWith({ [UNSIGNED_SIMULATOR_ENV]: '1', APP_ENV: appEnv })).toThrow(
      UNSIGNED_SIMULATOR_ENV,
    );
  });
});

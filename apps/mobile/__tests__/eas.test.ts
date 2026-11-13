import eas from '../eas.json';

type BuildProfile = {
  developmentClient?: boolean;
  distribution?: string;
  channel?: string;
  autoIncrement?: boolean;
  extends?: string;
  env?: Record<string, string>;
  ios?: { simulator?: boolean };
};

const build = eas.build as Record<string, BuildProfile>;

describe('eas.json', () => {
  it('lets EAS own build numbers', () => {
    expect(eas.cli.appVersionSource).toBe('remote');
  });

  it('has development, preview and production profiles, each with its own channel and APP_ENV', () => {
    for (const profile of ['development', 'preview', 'production']) {
      expect(build[profile]?.channel).toBe(profile);
      expect(build[profile]?.env?.APP_ENV).toBe(profile);
    }
  });

  it('builds the development profile as an internal dev client', () => {
    expect(build.development).toMatchObject({ developmentClient: true, distribution: 'internal' });
  });

  it('has a simulator variant of the development profile for Orbit on the iOS simulator', () => {
    expect(build['development-simulator']).toMatchObject({
      extends: 'development',
      ios: { simulator: true },
    });
  });

  it('distributes preview internally and auto-increments production', () => {
    expect(build.preview?.distribution).toBe('internal');
    expect(build.preview?.developmentClient).toBeUndefined();
    expect(build.production?.autoIncrement).toBe(true);
    expect(build.production?.developmentClient).toBeUndefined();
  });
});

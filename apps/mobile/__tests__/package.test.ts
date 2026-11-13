import pkg from '../package.json';

describe('package.json', () => {
  it('starts Metro for the development client, not Expo Go', () => {
    expect(pkg.scripts.start).toBe('expo start --dev-client');
  });

  it('enters through Expo Router', () => {
    expect(pkg.main).toBe('expo-router/entry');
  });
});

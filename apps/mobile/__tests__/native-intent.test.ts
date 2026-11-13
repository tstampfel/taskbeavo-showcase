import { redirectSystemPath } from '../app/+native-intent';

describe('+native-intent', () => {
  it('opens the screen a known link names, on launch and while running', () => {
    for (const initial of [true, false]) {
      expect(
        redirectSystemPath({ path: 'https://taskbeavo.com/de/bookings/7/release', initial }),
      ).toBe('/bookings/7/release');
    }
  });

  it('opens the home screen when the app is launched from an unknown link', () => {
    expect(redirectSystemPath({ path: 'https://evil.example/bookings/7', initial: true })).toBe(
      '/',
    );
    expect(redirectSystemPath({ path: 'taskbeavo:///', initial: true })).toBe('/');
  });

  it('ignores an unknown link while the app is running, keeping the current screen', () => {
    expect(
      redirectSystemPath({ path: 'taskbeavo://settings/delete-account', initial: false }),
    ).toBeNull();
  });
});

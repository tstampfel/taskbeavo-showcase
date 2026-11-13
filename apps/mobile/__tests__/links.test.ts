import { appPathFromLink } from '../src/links/routes';

describe('appPathFromLink', () => {
  it.each([
    ['https://taskbeavo.com/tasks/42', '/tasks/42'],
    ['https://www.taskbeavo.com/bookings/7', '/bookings/7'],
    ['https://taskbeavo.com/bookings/7/release', '/bookings/7/release'],
    ['https://taskbeavo.com/bookings/7/no-access', '/bookings/7/no-access'],
    ['https://taskbeavo.com/conversations/3', '/conversations/3'],
    // Stripe's onboarding returns to the web URL; the app opens it.
    ['https://taskbeavo.com/helper/payouts/return', '/helper/payouts/return'],
    ['https://taskbeavo.com/de/helper/payouts/refresh', '/helper/payouts/refresh'],
    // Emails link to the web page in the recipient's language.
    ['https://taskbeavo.com/de/bookings/7/release', '/bookings/7/release'],
    ['https://taskbeavo.com/hr/tasks/42', '/tasks/42'],
    ['https://taskbeavo.com/fr/bookings/7/', '/bookings/7'],
    // The app's own scheme, as `npx uri-scheme open` and push routes use it.
    ['taskbeavo://bookings/7/release', '/bookings/7/release'],
    ['taskbeavo:///tasks/42', '/tasks/42'],
    // A push's data.route, already a path.
    ['/bookings/7/no-access', '/bookings/7/no-access'],
    ['/en/tasks/42', '/tasks/42'],
    ['/helper/payouts', '/helper/payouts'],
  ])('opens %s at %s', (link, path) => {
    expect(appPathFromLink(link)).toBe(path);
  });

  it('drops the query string and fragment, so a link can never pass an action', () => {
    expect(appPathFromLink('https://taskbeavo.com/bookings/7/release?confirm=1&tip=500')).toBe(
      '/bookings/7/release',
    );
    expect(appPathFromLink('taskbeavo://bookings/7/release#release')).toBe('/bookings/7/release');
  });

  it.each([
    'https://evil.example/bookings/7/release',
    'https://taskbeavo.com.evil.example/tasks/1',
    'http://taskbeavo.com/tasks/1',
    'https://taskbeavo.com/bookings/7/release/confirm',
    'https://taskbeavo.com/bookings/7/pay',
    'https://taskbeavo.com/admin',
    'https://taskbeavo.com/tasks/',
    'https://taskbeavo.com/tasks/..%2Fadmin',
    'https://taskbeavo.com/it/tasks/42',
    'taskbeavo://settings/delete-account',
    'javascript:alert(1)',
    '',
    'not a url',
  ])('sends %s to the home screen', (link) => {
    expect(appPathFromLink(link)).toBe('/');
  });

  it('keeps the root link on the home screen', () => {
    expect(appPathFromLink('taskbeavo:///')).toBe('/');
    expect(appPathFromLink('https://taskbeavo.com/de')).toBe('/');
  });
});

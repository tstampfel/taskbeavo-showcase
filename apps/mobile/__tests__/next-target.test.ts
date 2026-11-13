import { nextTarget } from '../src/links/next-target';

describe('nextTarget', () => {
  it('keeps a screen a link may open', () => {
    expect(nextTarget('/bookings/12/release')).toBe('/bookings/12/release');
    expect(nextTarget(['/tasks/7'])).toBe('/tasks/7');
  });

  it('goes home for anything else', () => {
    expect(nextTarget(undefined)).toBe('/');
    expect(nextTarget('/settings/delete-account')).toBe('/');
    expect(nextTarget('https://evil.example/tasks/7')).toBe('/');
  });
});

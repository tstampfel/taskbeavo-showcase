import { describe, expect, it } from 'vitest';
import { timeAgo } from './time-ago';

const NOW = new Date('2026-11-05T10:00:00Z');
const before = (ms: number) => new Date(NOW.getTime() - ms).toISOString();

describe('timeAgo', () => {
  it('counts minutes under an hour, hours under a day, then days', () => {
    expect(timeAgo(before(20 * 60_000), NOW, 'en')).toBe('20 minutes ago');
    expect(timeAgo(before(2 * 3_600_000), NOW, 'en')).toBe('2 hours ago');
    expect(timeAgo(before(26 * 3_600_000), NOW, 'en')).toBe('yesterday');
    expect(timeAgo(before(3 * 86_400_000), NOW, 'de')).toBe('vor 3 Tagen');
  });

  it('reads a time just now or slightly in the future as now', () => {
    expect(timeAgo(before(10_000), NOW, 'en')).toBe('now');
    expect(timeAgo(before(-30_000), NOW, 'fr')).toBe('maintenant');
  });
});

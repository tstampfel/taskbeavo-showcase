import { describe, expect, it } from 'vitest';
import { formatDate, formatDateTime, formatMoney, formatPercent, TASK_TIME_ZONE } from './format';

// Intl separates numbers from units with no-break spaces; compare with plain spaces.
const plain = (text: string) => text.replace(/[\u00a0\u202f]/g, ' ');

describe('formatMoney', () => {
  it('formats euro cents the way each language writes money', () => {
    expect(plain(formatMoney(3360, 'hr'))).toBe('33,60 €');
    expect(plain(formatMoney(3360, 'en'))).toBe('€33.60');
    expect(plain(formatMoney(3360, 'de'))).toBe('33,60 €');
    expect(plain(formatMoney(3360, 'fr'))).toBe('33,60 €');
  });

  it('groups thousands and always shows cents', () => {
    expect(plain(formatMoney(123400, 'de'))).toBe('1.234,00 €');
    expect(plain(formatMoney(123400, 'en'))).toBe('€1,234.00');
  });
});

describe('formatPercent', () => {
  it('writes a whole percent the way each language does', () => {
    expect(plain(formatPercent(12, 'en'))).toBe('12%');
    expect(plain(formatPercent(12, 'hr'))).toBe('12 %');
    expect(plain(formatPercent(12, 'de'))).toBe('12 %');
    expect(plain(formatPercent(12, 'fr'))).toBe('12 %');
  });
});

describe('formatDateTime', () => {
  const start = '2026-11-03T09:30:00Z';

  it('shows task times in Zagreb time, whatever the viewer’s time zone', () => {
    expect(TASK_TIME_ZONE).toBe('Europe/Zagreb');
    expect(plain(formatDateTime(start, 'de', 'full'))).toBe('Dienstag, 3. November 2026 um 10:30');
    expect(plain(formatDateTime(new Date(start), 'hr', 'full'))).toBe(
      'utorak, 3. studenoga 2026. u 10:30',
    );
  });

  it('uses each language’s own short format', () => {
    expect(plain(formatDateTime(start, 'en'))).toBe('Nov 3, 2026, 10:30 AM');
    expect(plain(formatDateTime(start, 'fr'))).toBe('3 nov. 2026, 10:30');
  });
});

describe('formatDate', () => {
  it('formats the calendar day in Zagreb', () => {
    // 23:30 UTC on 3 November is already 4 November in Zagreb.
    expect(plain(formatDate('2026-11-03T23:30:00Z', 'de'))).toBe('04.11.2026');
    expect(plain(formatDate('2026-11-03T23:30:00Z', 'hr', 'full'))).toBe(
      'srijeda, 4. studenoga 2026.',
    );
  });
});

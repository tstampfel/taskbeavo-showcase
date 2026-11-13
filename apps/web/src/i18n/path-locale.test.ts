import { describe, expect, it } from 'vitest';
import { pathLocale } from './path-locale';

describe('pathLocale', () => {
  it('uses the locale in the path, the same on the server and in the browser', () => {
    expect(pathLocale('/de/tasks/4')).toBe('de');
    expect(pathLocale('/fr')).toBe('fr');
  });

  it('falls back to Croatian', () => {
    expect(pathLocale('/robots.txt')).toBe('hr');
    expect(pathLocale(null)).toBe('hr');
  });
});

import { describe, expect, it } from 'vitest';
import { loadPickedLocale, savePickedLocale } from './picked-locale';

function memory() {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => void values.set(key, value),
    removeItem: (key: string) => void values.delete(key),
    values,
  };
}

describe('picked locale', () => {
  it('remembers and forgets the explicit pick', () => {
    const store = memory();
    expect(loadPickedLocale(store)).toBeNull();
    savePickedLocale('de', store);
    expect(loadPickedLocale(store)).toBe('de');
    savePickedLocale(null, store);
    expect(loadPickedLocale(store)).toBeNull();
  });

  it('ignores values it does not know and storage that throws', () => {
    const store = memory();
    store.values.set('taskbeavo.pickedLocale', 'it');
    expect(loadPickedLocale(store)).toBeNull();
    const broken = {
      getItem: () => {
        throw new Error('blocked');
      },
      setItem: () => {
        throw new Error('blocked');
      },
      removeItem: () => {},
    };
    expect(loadPickedLocale(broken)).toBeNull();
    expect(() => savePickedLocale('fr', broken)).not.toThrow();
  });
});

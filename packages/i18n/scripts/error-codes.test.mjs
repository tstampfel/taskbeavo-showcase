import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { readErrorCodes } from './check-catalogues.mjs';

const packageDir = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const schemaPath = resolve(packageDir, '../graphql/schema.graphql');

/** Translations under `errors` in one locale's catalogue. */
function errorsIn(locale) {
  const path = resolve(packageDir, `src/locales/${locale}.json`);
  return JSON.parse(readFileSync(path, 'utf8')).errors ?? {};
}

describe('API error codes', () => {
  const codes = readErrorCodes(schemaPath);

  it('are read from schema.graphql', () => {
    expect(codes).toContain('RATE_LIMITED');
  });

  it.each(['hr', 'en', 'de', 'fr'])('each has errors.<CODE> in %s', (locale) => {
    const errors = errorsIn(locale);
    expect(codes.filter((code) => !(code in errors))).toEqual([]);
  });
});

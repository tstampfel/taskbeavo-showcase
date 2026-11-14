// Writes src/country-names.json: every country's name in hr, en, de and fr, from the CLDR data
// in Node's ICU (Intl.DisplayNames). The app needs the table because Hermes has no
// Intl.DisplayNames, and without it the country list shows bare codes (BEAV-223).
// Run with Node built with full ICU (the default): node scripts/generate-country-names.mjs
import { readFileSync, writeFileSync } from 'node:fs';

const source = readFileSync(new URL('../src/countries.ts', import.meta.url), 'utf8');
const block = /export const countryCodes[^=]*=\s*\(([\s\S]*?)\)\.split/.exec(source)?.[1];
if (!block) throw new Error('countryCodes not found in src/countries.ts');
const codes = [...block.matchAll(/'([A-Z ]+)'/g)]
  .map(([, part]) => part)
  .join('')
  .split(' ')
  .filter(Boolean);

const names = {};
for (const locale of ['hr', 'en', 'de', 'fr']) {
  const display = new Intl.DisplayNames([locale], { type: 'region', fallback: 'none' });
  names[locale] = {};
  for (const code of codes) {
    const name = display.of(code);
    if (!name || name === code) throw new Error(`${locale}: no name for ${code}`);
    names[locale][code] = name;
  }
}
const out = new URL('../src/country-names.json', import.meta.url);
writeFileSync(out, `${JSON.stringify(names, null, 2)}\n`);
console.log(`wrote ${codes.length} countries in ${Object.keys(names).length} languages`);

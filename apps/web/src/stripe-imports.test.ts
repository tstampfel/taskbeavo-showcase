import { readdirSync, readFileSync } from 'node:fs';
import { join, relative } from 'node:path';
import { describe, expect, it } from 'vitest';
import { STRIPE_JS_MODULES } from '../eslint.config.js';

const root = join(import.meta.dirname, '..');

function sources(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return sources(path);
    return /\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name) ? [path] : [];
  });
}

const files = sources(join(root, 'src')).map((path) => ({
  path: relative(root, path),
  text: readFileSync(path, 'utf8'),
}));

// A value import, re-export or dynamic import() (not `import type`) of Stripe.js or its React
// bindings (review R1-7).
const STRIPE_VALUE_IMPORT =
  /^(?:import|export)\s+(?!type\b)[^;]*from\s+'@stripe\/(stripe-js|react-stripe-js)|\bimport\(\s*'@stripe\/(stripe-js|react-stripe-js)/m;

describe('Stripe.js stays on the checkout and payment pages (BEAV-190)', () => {
  it('recognises every way of loading it', () => {
    expect(STRIPE_VALUE_IMPORT.test("import { loadStripe } from '@stripe/stripe-js';")).toBe(true);
    expect(STRIPE_VALUE_IMPORT.test("export { Elements } from '@stripe/react-stripe-js';")).toBe(
      true,
    );
    expect(STRIPE_VALUE_IMPORT.test("const s = await import('@stripe/stripe-js');")).toBe(true);
    expect(STRIPE_VALUE_IMPORT.test("import type { Stripe } from '@stripe/stripe-js';")).toBe(
      false,
    );
    expect(STRIPE_VALUE_IMPORT.test("export type { Stripe } from '@stripe/stripe-js';")).toBe(
      false,
    );
  });

  it('is imported only by the allowed modules', () => {
    const importers = files.filter((f) => STRIPE_VALUE_IMPORT.test(f.text)).map((f) => f.path);
    expect(importers.sort()).toEqual([...STRIPE_JS_MODULES].sort());
  });

  it('reaches only checkout pages through those modules', () => {
    const checkout = STRIPE_JS_MODULES.filter((path) => path.includes('/checkout/'));
    for (const allowed of checkout) {
      const name = allowed.replace(/^.*\//, '').replace(/\.tsx?$/, '');
      const importers = files
        .filter((f) => new RegExp(`from\\s+'[^']*/${name}'`).test(f.text))
        .map((f) => f.path);
      expect(importers.length).toBeGreaterThan(0);
      for (const importer of importers) {
        expect(importer).toMatch(/^src\/app\/\[locale\]\/.*\/checkout\/page\.tsx$/);
      }
    }
  });

  it('loads the tip form only lazily, from the release page, when a tip is paid (BEAV-118)', () => {
    const statically = files.filter((f) => /from\s+'[^']*\/tip-payment'/.test(f.text));
    expect(statically.map((f) => f.path)).toEqual([]);
    const lazily = files.filter((f) => /import\(\s*'[^']*\/tip-payment'\s*\)/.test(f.text));
    expect(lazily.map((f) => f.path)).toEqual(['src/components/release/booking-release.tsx']);
  });
});

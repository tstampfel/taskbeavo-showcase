import { catalogues, locales } from '@taskbeavo/i18n';
import { typography } from '@taskbeavo/ui-tokens';
import { render, screen } from '@testing-library/react-native';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { Dimensions, Text } from 'react-native';
import {
  CARD_HEADING_INSET,
  Heading,
  charEm,
  longestRunEm,
  textOf,
  unbreakableRuns,
  wordFitFontScale,
} from '../src/components/ui/heading';

const IPHONE_SE_WIDTH = 375;
/** iOS's largest accessibility text size (AX5) scales 17pt body text to 53pt. */
const AX5_FONT_SCALE = 53 / 17;

beforeEach(() => {
  jest
    .spyOn(Dimensions, 'get')
    .mockReturnValue({ width: 402, height: 874, scale: 3, fontScale: AX5_FONT_SCALE });
});
afterEach(() => jest.restoreAllMocks());

/** Advance widths, in em, read from the Manrope Bold file the app bundles (cmap 4 and hmtx). */
function manropeBoldAdvances(): (char: string) => number {
  const font = readFileSync(
    require.resolve('@expo-google-fonts/manrope/700Bold/Manrope_700Bold.ttf'),
  );
  const tables = new Map<string, number>();
  for (let i = 0; i < font.readUInt16BE(4); i += 1) {
    const record = 12 + 16 * i;
    tables.set(font.toString('latin1', record, record + 4), font.readUInt32BE(record + 8));
  }
  const table = (tag: string) => tables.get(tag) ?? 0;
  const unitsPerEm = font.readUInt16BE(table('head') + 18);
  const metricCount = font.readUInt16BE(table('hhea') + 34);
  const advance = (glyph: number) =>
    font.readUInt16BE(table('hmtx') + 4 * Math.min(glyph, metricCount - 1)) / unitsPerEm;
  const cmap = table('cmap');
  let format4 = 0;
  for (let i = 0; i < font.readUInt16BE(cmap + 2); i += 1) {
    const subtable = cmap + font.readUInt32BE(cmap + 8 + 8 * i);
    if (font.readUInt16BE(subtable) === 4) format4 = subtable;
  }
  const segments = font.readUInt16BE(format4 + 6) / 2;
  const ends = format4 + 14;
  const starts = ends + 2 * segments + 2;
  const deltas = starts + 2 * segments;
  const offsets = deltas + 2 * segments;
  return (char) => {
    const code = char.codePointAt(0) ?? 0;
    for (let k = 0; k < segments; k += 1) {
      if (code > font.readUInt16BE(ends + 2 * k)) continue;
      const start = font.readUInt16BE(starts + 2 * k);
      if (code < start) return advance(0);
      const delta = font.readInt16BE(deltas + 2 * k);
      const offset = font.readUInt16BE(offsets + 2 * k);
      if (offset === 0) return advance((code + delta) & 0xffff);
      const glyph = font.readUInt16BE(offsets + 2 * k + offset + 2 * (code - start));
      return advance(glyph === 0 ? 0 : (glyph + delta) & 0xffff);
    }
    return advance(0);
  };
}

function catalogueStrings(value: unknown): string[] {
  if (typeof value === 'string') return [value];
  if (value && typeof value === 'object') return Object.values(value).flatMap(catalogueStrings);
  return [];
}

describe('word width estimate', () => {
  it('is never narrower than Manrope Bold draws any character in the catalogues', () => {
    const advance = manropeBoldAdvances();
    const chars = new Set(
      locales.flatMap((locale) => catalogueStrings(catalogues[locale]).flatMap((s) => [...s])),
    );
    for (const char of chars) {
      if (/\s/.test(char)) continue;
      expect({ char, em: charEm(char) >= advance(char) }).toEqual({ char, em: true });
    }
  });

  it('splits at spaces and after hyphens, but not at no-break spaces', () => {
    expect(unbreakableRuns('Otkazivanje  rezervacije')).toEqual(['Otkazivanje', 'rezervacije']);
    expect(unbreakableRuns('E-Mail-Adresse')).toEqual(['E-', 'Mail-', 'Adresse']);
    expect(unbreakableRuns('12 € total')).toEqual(['12 €', 'total']);
  });

  it('measures the widest run, counting accents like their letters', () => {
    expect(longestRunEm('Rückerstattung bestätigt')).toBe(longestRunEm('Ruckerstattung'));
    expect(longestRunEm('')).toBe(0);
  });
});

describe('wordFitFontScale', () => {
  it('stops growing where the longest word would no longer fit on a line', () => {
    const scale = wordFitFontScale('Stornierungsbedingungen akzeptiert', 20, 338);
    expect(longestRunEm('Stornierungsbedingungen') * 20 * scale).toBeLessThanOrEqual(338);
    expect(scale).toBeLessThan(AX5_FONT_SCALE);
  });

  it('never caps below the design size, and keeps a stricter cap the layout has', () => {
    expect(wordFitFontScale('Donaudampfschifffahrtsgesellschaft', 24, 200)).toBe(1);
    expect(wordFitFontScale('Hi', 16, 338, 1.6)).toBe(1.6);
    expect(wordFitFontScale('Hi', 16, 338)).toBeGreaterThan(AX5_FONT_SCALE);
  });

  it('fixes the bug from BEAV-201: "cancelling" fits at AX5 on the narrowest iPhone', () => {
    const width = IPHONE_SE_WIDTH - CARD_HEADING_INSET;
    const scale = Math.min(AX5_FONT_SCALE, wordFitFontScale('Booking cancelling', 20, width));
    expect(longestRunEm('cancelling') * typography.h2.fontSize * scale).toBeLessThanOrEqual(width);
  });
});

describe('Heading', () => {
  it('is a header whose text scale is capped where its longest word fits', async () => {
    await render(
      <Heading style={{ fontSize: typography.h2.fontSize }}>Widerrufsbelehrung gelesen</Heading>,
    );
    const heading = screen.getByRole('header', { name: 'Widerrufsbelehrung gelesen' });
    expect(heading.props.maxFontSizeMultiplier).toBe(
      wordFitFontScale(
        'Widerrufsbelehrung gelesen',
        typography.h2.fontSize,
        402 - CARD_HEADING_INSET,
      ),
    );
  });

  it('uses its inset and the nested text', async () => {
    await render(
      <Heading inset={136} maxScale={1.6} style={[{ fontSize: 16 }, { color: 'red' }]}>
        Annulation <Text>confirmée</Text>
      </Heading>,
    );
    expect(screen.getByRole('header').props.maxFontSizeMultiplier).toBe(
      wordFitFontScale('Annulation confirmée', 16, 402 - 136, 1.6),
    );
  });

  it('reads plain text from children', () => {
    expect(textOf(['Otkaz ', 3, <Text key="a">dana</Text>, null])).toBe('Otkaz 3dana');
  });
});

/** Translation keys rendered inside a <Heading> anywhere in the app. */
function headingKeys(): string[] {
  const root = join(__dirname, '..');
  const files: string[] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const path = join(dir, name);
      if (statSync(path).isDirectory()) walk(path);
      else if (/\.tsx$/.test(name)) files.push(path);
    }
  };
  walk(join(root, 'src'));
  walk(join(root, 'app'));
  const keys = new Set<string>();
  for (const file of files) {
    const source = readFileSync(file, 'utf8');
    for (const [element] of source.matchAll(/<Heading\b[\s\S]*?<\/Heading>/g)) {
      for (const [, key] of element.matchAll(/\bt\(\s*[`']([\w.]+)[`']/g)) keys.add(key!);
    }
  }
  return [...keys];
}

function message(locale: (typeof locales)[number], key: string): string {
  const value = key
    .split('.')
    .reduce<unknown>((node, part) => (node as Record<string, unknown>)?.[part], catalogues[locale]);
  if (typeof value !== 'string') throw new Error(`${locale}: ${key} is not a message`);
  // ICU placeholders become a short value: the words around them are what can be too long.
  return value.replace(/\{[^{}]*\}/g, '0');
}

describe('heading copy in hr, en, de and fr', () => {
  const keys = headingKeys();

  it('finds the headings', () => {
    expect(keys).toEqual(
      expect.arrayContaining(['cancel.helper.title', 'cancel.withdrawal.title']),
    );
  });

  it.each(locales)(
    '%s: every heading word fits a card on the narrowest iPhone at h1 size',
    (locale) => {
      const width = IPHONE_SE_WIDTH - CARD_HEADING_INSET;
      const tooLong = keys.filter(
        (key) => longestRunEm(message(locale, key)) * typography.h1.fontSize > width,
      );
      expect(tooLong).toEqual([]);
    },
  );
});

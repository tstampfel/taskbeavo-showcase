import { typography } from '@taskbeavo/ui-tokens';
import { render, screen } from '@testing-library/react-native';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { Dimensions, StyleSheet } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { AmountRow, Heading, Screen, Text, TextScaleScope } from '../src/components/ui';
import {
  CARD_HEADING_INSET,
  fitsOnOneLine,
  lineEm,
  longestRunEm,
  wordFitFontScale,
} from '../src/components/ui/heading';
import { headingCap } from '../src/components/ui/text-scale-scope';

/** iOS's largest accessibility text size (AX5) scales 17pt body text to 53pt. */
const AX5 = 53 / 17;
const WIDTH = 402;
const metrics = {
  frame: { x: 0, y: 0, width: WIDTH, height: 874 },
  insets: { top: 47, left: 0, right: 0, bottom: 34 },
};

function atFontScale(fontScale: number) {
  jest.spyOn(Dimensions, 'get').mockReturnValue({ width: WIDTH, height: 874, scale: 3, fontScale });
}
afterEach(() => jest.restoreAllMocks());

/** The size a text renders at: its font size at the user's scale, up to its cap. */
function renderedSize(element: ReturnType<typeof screen.getByText>, fontScale = AX5): number {
  const fontSize = StyleSheet.flatten(element.props.style).fontSize as number;
  return fontSize * Math.min(fontScale, element.props.maxFontSizeMultiplier as number);
}

describe('Text', () => {
  beforeEach(() => atFontScale(AX5));

  it('grows only as far as its longest word fits, as in "Remboursement" (BEAV-223)', async () => {
    const text = 'Remboursement sur votre carte sous 5 à 10 jours ouvrés';
    await render(<Text style={{ fontSize: typography.small.fontSize }}>{text}</Text>);
    const element = screen.getByText(text);
    const scale = element.props.maxFontSizeMultiplier as number;
    expect(scale).toBe(
      wordFitFontScale(text, typography.small.fontSize, WIDTH - CARD_HEADING_INSET),
    );
    expect(scale).toBeLessThan(AX5);
    expect(longestRunEm(text) * renderedSize(element)).toBeLessThanOrEqual(
      WIDTH - CARD_HEADING_INSET,
    );
  });

  it('never shrinks below its design size, and short text keeps the full scale', async () => {
    await render(
      <>
        <Text style={{ fontSize: 40 }}>Donaudampfschifffahrtsgesellschaft</Text>
        <Text style={{ fontSize: 16 }}>Oui</Text>
      </>,
    );
    expect(screen.getByText('Donaudampfschifffahrtsgesellschaft').props.maxFontSizeMultiplier).toBe(
      1,
    );
    expect(screen.getByText('Oui').props.maxFontSizeMultiplier).toBeGreaterThan(AX5);
  });

  it('uses its inset or line width, and keeps a stricter cap it is given', async () => {
    await render(
      <>
        <Text style={{ fontSize: 12 }} inset={120}>
          Paiement demandé
        </Text>
        <Text style={{ fontSize: 12 }} lineWidth={80}>
          Versé
        </Text>
        <Text style={{ fontSize: 12 }} maxFontSizeMultiplier={1.5}>
          HR
        </Text>
      </>,
    );
    expect(screen.getByText('Paiement demandé').props.maxFontSizeMultiplier).toBe(
      wordFitFontScale('Paiement demandé', 12, WIDTH - 120),
    );
    expect(screen.getByText('Versé').props.maxFontSizeMultiplier).toBe(
      wordFitFontScale('Versé', 12, 80),
    );
    expect(screen.getByText('HR').props.maxFontSizeMultiplier).toBe(1.5);
  });
});

describe('headings and body text', () => {
  beforeEach(() => atFontScale(AX5));

  it('caps text at the tightest heading, or its rendered size when it is bigger by design', () => {
    expect(headingCap([], 16)).toBe(Infinity);
    // An h2 (20pt) that stops at 1.5x renders at 30pt: 16pt text may grow to 30pt too.
    expect(headingCap([{ scale: 1.5, fontSize: 20 }], 16)).toBeCloseTo(30 / 16);
    // A 12pt label heading at 2x: 16pt text grows no further than 2x either.
    expect(headingCap([{ scale: 2, fontSize: 12 }], 16)).toBe(2);
    expect(
      headingCap(
        [
          { scale: 3, fontSize: 20 },
          { scale: 1.2, fontSize: 24 },
        ],
        16,
      ),
    ).toBeCloseTo(1.8);
  });

  it('never lets body text read bigger than the heading above it (BEAV-215 review)', async () => {
    // A long heading word stops the heading early; the short reason alone would grow to AX5.
    const legend = 'Stornierungsbedingungen';
    const reason = 'Ja';
    await render(
      <TextScaleScope>
        <Heading style={{ fontSize: typography.h2.fontSize }}>{legend}</Heading>
        <Text style={{ fontSize: typography.body.fontSize }}>{reason}</Text>
      </TextScaleScope>,
    );
    const heading = screen.getByRole('header', { name: legend });
    const body = screen.getByText(reason);
    // Without the scope the reason grew to its own word-fit size, past the heading.
    const unscoped = wordFitFontScale(reason, typography.body.fontSize, WIDTH - CARD_HEADING_INSET);
    expect(typography.body.fontSize * Math.min(AX5, unscoped)).toBeGreaterThan(
      renderedSize(heading),
    );
    expect(renderedSize(body)).toBeLessThanOrEqual(renderedSize(heading) + 1e-9);
  });

  it('keeps a small label heading’s proportion to the body text beside it', async () => {
    await render(
      <TextScaleScope>
        <Heading style={{ fontSize: 12 }} maxScale={1.4}>
          VOTRE INTERVENANT
        </Heading>
        <Text style={{ fontSize: 16 }}>Contact</Text>
      </TextScaleScope>,
    );
    expect(screen.getByText('Contact').props.maxFontSizeMultiplier).toBe(1.4);
  });

  it('caps the text of a screen body by its headings, but not by the top bar title', async () => {
    await render(
      <SafeAreaProvider initialMetrics={metrics}>
        <Screen title="Ok" onBack={jest.fn()} backLabel="Back">
          <Heading style={{ fontSize: typography.h2.fontSize }}>Rückerstattung</Heading>
          <Text style={{ fontSize: typography.body.fontSize }}>Ja</Text>
        </Screen>
      </SafeAreaProvider>,
    );
    const heading = screen.getByRole('header', { name: 'Rückerstattung' });
    expect(renderedSize(screen.getByText('Ja'))).toBeCloseTo(renderedSize(heading));
    // The top bar title is capped at 1.6x for its narrow bar; the body text is not held to it.
    expect(renderedSize(screen.getByText('Ja'))).toBeGreaterThan(typography.body.fontSize * 1.6);
  });
});

describe('AmountRow', () => {
  const label = 'Vous récupérez';
  const amount = '50,40 €';

  it('keeps label and amount on one line while they fit', async () => {
    atFontScale(1);
    await render(<AmountRow label={label} value={amount} style={{ fontSize: 14 }} />);
    const row = screen.getByLabelText(`${label}: ${amount}`);
    expect(StyleSheet.flatten(row.props.style)).toMatchObject({ flexDirection: 'row' });
  });

  it('puts the amount under the label, on the right, when they no longer fit (BEAV-223)', async () => {
    atFontScale(AX5);
    await render(<AmountRow label={label} value={amount} style={{ fontSize: 14 }} />);
    const row = screen.getByLabelText(`${label}: ${amount}`);
    expect(StyleSheet.flatten(row.props.style).flexDirection).toBeUndefined();
    const value = screen.getByText(amount);
    expect(StyleSheet.flatten(value.props.style)).toMatchObject({
      alignSelf: 'flex-end',
      textAlign: 'right',
    });
    // The label has the whole line, so "récupérez" fits on it.
    const shown = screen.getByText(label);
    expect(longestRunEm(label) * renderedSize(shown)).toBeLessThanOrEqual(
      WIDTH - CARD_HEADING_INSET,
    );
    // The amount is shown exactly as formatted, never broken.
    expect(value.props.children).toBe(amount);
    expect(longestRunEm(amount) * renderedSize(value)).toBeLessThanOrEqual(
      WIDTH - CARD_HEADING_INSET,
    );
  });

  it('decides with an estimate that is never narrower than the text', () => {
    expect(lineEm('ab cd')).toBeGreaterThan(longestRunEm('ab cd'));
    expect(fitsOnOneLine([{ text: 'Total', fontSize: 16 }], 0, 200, 1)).toBe(true);
    expect(fitsOnOneLine([{ text: 'Total', fontSize: 16 }], 0, 40, 1)).toBe(false);
  });
});

/**
 * Every component file outside the text components themselves takes `Text` from
 * `components/ui/text`: React Native's own Text has no word-fit cap and no heading cap.
 */
describe('the app’s text', () => {
  it('comes from components/ui/text everywhere', () => {
    const root = join(__dirname, '..');
    const allowed = new Set([
      'src/components/ui/text.tsx',
      'src/components/ui/heading.tsx',
      // The button caps its own label (BEAV-219).
      'src/components/ui/button.tsx',
    ]);
    const offenders: string[] = [];
    const walk = (dir: string) => {
      for (const name of readdirSync(dir)) {
        const path = join(dir, name);
        if (statSync(path).isDirectory()) walk(path);
        else if (/\.tsx$/.test(name)) {
          const file = relative(root, path);
          const imports = /import\s*\{([^}]*)\}\s*from\s*'react-native'/.exec(
            readFileSync(path, 'utf8'),
          );
          const names = imports?.[1]?.split(',').map((n) => n.trim()) ?? [];
          if (names.includes('Text') && !allowed.has(file)) offenders.push(file);
        }
      }
    };
    walk(join(root, 'src'));
    walk(join(root, 'app'));
    expect(offenders).toEqual([]);
  });
});

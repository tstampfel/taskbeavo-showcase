import { describe, expect, it } from 'vitest';
import {
  buttonDisabled,
  buttonVariants,
  colors,
  contrastPairs,
  contrastRatio,
  controlHeight,
  cssVariables,
  fontFamily,
  minTapTarget,
  rem,
  typography,
} from './index';

describe('brand tokens', () => {
  it('match the brand palette', () => {
    expect(colors).toMatchObject({
      green: '#1F5C46',
      orange: '#F2A33A',
      offWhite: '#F5F4EF',
      ink: '#17211C',
    });
  });

  it('use Manrope', () => {
    expect(fontFamily).toBe('Manrope');
  });

  it('keep tap targets at least 44px', () => {
    expect(minTapTarget).toBeGreaterThanOrEqual(44);
    for (const height of Object.values(controlHeight)) {
      expect(height).toBeGreaterThanOrEqual(minTapTarget);
    }
  });

  it('give every text style a line height of at least its font size', () => {
    for (const style of Object.values(typography)) {
      expect(style.lineHeight).toBeGreaterThanOrEqual(style.fontSize);
    }
  });
});

describe('contrastRatio', () => {
  it('matches the WCAG reference values', () => {
    expect(contrastRatio('#000000', '#FFFFFF')).toBeCloseTo(21, 5);
    expect(contrastRatio('#FFFFFF', '#FFFFFF')).toBeCloseTo(1, 5);
    expect(contrastRatio('#777777', '#FFFFFF')).toBeCloseTo(4.48, 2);
  });

  it('is symmetric', () => {
    expect(contrastRatio(colors.ink, colors.offWhite)).toBe(
      contrastRatio(colors.offWhite, colors.ink),
    );
  });

  it('rejects colours it cannot measure', () => {
    expect(() => contrastRatio('transparent', '#FFFFFF')).toThrow();
  });
});

describe('contrast (WCAG AA)', () => {
  it.each(contrastPairs.map((pair) => [pair.name, pair] as const))('%s', (_name, pair) => {
    expect(contrastRatio(pair.foreground, pair.background)).toBeGreaterThanOrEqual(pair.min);
  });

  it('covers every button variant and the disabled state', () => {
    const names = contrastPairs.map((pair) => pair.name);
    for (const variant of Object.keys(buttonVariants)) {
      expect(names.some((name) => name.startsWith(`${variant} button label`))).toBe(true);
    }
    expect(names).toContain('disabled button label');
    expect(buttonDisabled.foreground).not.toBe(colors.white);
  });

  it('documents that brand orange is not a text colour', () => {
    expect(contrastRatio(colors.orange, colors.offWhite)).toBeLessThan(4.5);
    expect(contrastRatio(colors.orangeText, colors.offWhite)).toBeGreaterThanOrEqual(4.5);
  });
});

describe('cssVariables', () => {
  const vars = cssVariables();

  it('exposes the brand colours', () => {
    expect(vars['--tb-color-green']).toBe(colors.green);
    expect(vars['--tb-color-off-white']).toBe(colors.offWhite);
    expect(vars['--tb-color-ink']).toBe(colors.ink);
  });

  it('sizes text in rem so it follows the browser text size', () => {
    expect(vars['--tb-font-body-size']).toBe(rem(typography.body.fontSize));
    expect(rem(16)).toBe('1rem');
  });

  it('exposes the tap target and button colours', () => {
    expect(vars['--tb-tap-target']).toBe('44px');
    expect(vars['--tb-button-primary-bg']).toBe(colors.green);
    expect(vars['--tb-button-disabled-fg']).toBe(buttonDisabled.foreground);
    expect(vars['--tb-shadow-card']).toBe('0 2px 8px rgba(31, 92, 70, 0.08)');
  });
});

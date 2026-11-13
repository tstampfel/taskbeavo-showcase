/**
 * Brand and component tokens shared by web and mobile. Every colour pair a component draws is
 * listed in `contrastPairs` and checked against WCAG AA in the tests; see README.md.
 */

export const colors = {
  // Brand
  green: '#1F5C46',
  orange: '#F2A33A',
  offWhite: '#F5F4EF',
  ink: '#17211C',
  // Surfaces and text
  white: '#FFFFFF',
  inkMuted: '#5E6661',
  /** Decorative dividers and card outlines only; never the sole boundary of a control. */
  line: '#E3E1D8',
  /** Control boundaries (input borders): 3:1 against white and off-white. */
  control: '#7D8580',
  /** Errors and destructive actions. The mockup's #C8452F is below 4.5:1 on off-white. */
  error: '#B23A26',
  /** Orange as text. Brand orange is only for fills and icons (1.9:1 on off-white). */
  orangeText: '#9A5E0C',
  // Tints for badges and notes
  greenTint: '#E3EFE9',
  orangeTint: '#FCEBD2',
  orangeTintText: '#8A5208',
  greyTint: '#ECEBE5',
  greyTintText: '#4A524D',
  redTint: '#F7E1DC',
  redTintText: '#A3361F',
  scrim: 'rgba(23, 33, 28, 0.45)',
} as const;

export type ColorToken = keyof typeof colors;

export const fontFamily = 'Manrope';

/** Minimum tap target in px (hard rule i). */
export const minTapTarget = 44;

/** Spacing scale in px. */
export const spacing = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32 } as const;

export const radii = { sm: 6, md: 12, lg: 16, sheet: 24, pill: 999 } as const;

export const borderWidth = { thin: 1, control: 1.5, focus: 2 } as const;

/** Heights are minimums: controls grow with the user's text size. */
export const controlHeight = { md: 52, sm: minTapTarget } as const;

export const fontWeights = {
  regular: 400,
  medium: 500,
  semibold: 600,
  bold: 700,
  extraBold: 800,
} as const;

export type FontWeight = (typeof fontWeights)[keyof typeof fontWeights];

interface TextStyleToken {
  /** px at 100% text size */
  fontSize: number;
  lineHeight: number;
  fontWeight: FontWeight;
}

export const typography = {
  display: { fontSize: 28, lineHeight: 34, fontWeight: fontWeights.extraBold },
  h1: { fontSize: 24, lineHeight: 30, fontWeight: fontWeights.bold },
  h2: { fontSize: 20, lineHeight: 26, fontWeight: fontWeights.bold },
  body: { fontSize: 16, lineHeight: 24, fontWeight: fontWeights.medium },
  button: { fontSize: 16, lineHeight: 22, fontWeight: fontWeights.bold },
  label: { fontSize: 14, lineHeight: 20, fontWeight: fontWeights.bold },
  small: { fontSize: 14, lineHeight: 20, fontWeight: fontWeights.medium },
  caption: { fontSize: 12, lineHeight: 16, fontWeight: fontWeights.semibold },
} as const satisfies Record<string, TextStyleToken>;

export type TypographyToken = keyof typeof typography;

export const shadows = {
  card: { color: colors.green, opacity: 0.08, offsetY: 2, blur: 8 },
  sheet: { color: colors.ink, opacity: 0.15, offsetY: -4, blur: 24 },
} as const;

interface ColorSet {
  background: string;
  foreground: string;
  border: string;
}

export const buttonVariants = {
  primary: { background: colors.green, foreground: colors.white, border: colors.green },
  secondary: { background: colors.white, foreground: colors.green, border: colors.green },
  ghost: { background: 'transparent', foreground: colors.green, border: 'transparent' },
  danger: { background: colors.white, foreground: colors.error, border: colors.error },
} as const satisfies Record<string, ColorSet>;

export type ButtonVariant = keyof typeof buttonVariants;

/** Disabled buttons keep readable text (WCAG exempts them, but older users still need to read them). */
export const buttonDisabled = {
  background: colors.line,
  foreground: colors.inkMuted,
  border: colors.line,
} as const satisfies ColorSet;

export const badgeTones = {
  green: { background: colors.greenTint, foreground: colors.green },
  orange: { background: colors.orangeTint, foreground: colors.orangeTintText },
  grey: { background: colors.greyTint, foreground: colors.greyTintText },
  red: { background: colors.redTint, foreground: colors.redTintText },
} as const;

export type BadgeTone = keyof typeof badgeTones;

export const inputColors = {
  background: colors.white,
  text: colors.ink,
  placeholder: colors.inkMuted,
  label: colors.ink,
  hint: colors.inkMuted,
  border: colors.control,
  focusBorder: colors.green,
  error: colors.error,
} as const;

/** Surfaces components sit on. */
export const surfaces = {
  page: colors.offWhite,
  card: colors.white,
  sheet: colors.white,
} as const;

export interface ContrastPair {
  name: string;
  foreground: string;
  background: string;
  /** 4.5 for text (WCAG 1.4.3), 3 for control boundaries and focus rings (WCAG 1.4.11). */
  min: 4.5 | 3;
}

const text = 4.5;
const nonText = 3;

function onSurfaces(name: string, foreground: string, min: 4.5 | 3): ContrastPair[] {
  return [
    { name: `${name} on page`, foreground, background: surfaces.page, min },
    { name: `${name} on card`, foreground, background: surfaces.card, min },
  ];
}

/** Every foreground/background pair the base components draw. */
export const contrastPairs: readonly ContrastPair[] = [
  ...onSurfaces('body text', colors.ink, text),
  ...onSurfaces('muted text', colors.inkMuted, text),
  ...onSurfaces('error text', colors.error, text),
  ...onSurfaces('orange text', colors.orangeText, text),
  ...onSurfaces('link text', colors.green, text),
  { name: 'off-white on green', foreground: colors.offWhite, background: colors.green, min: text },
  ...Object.entries(buttonVariants).flatMap(([variant, set]) =>
    set.background === 'transparent'
      ? onSurfaces(`${variant} button label`, set.foreground, text)
      : [
          {
            name: `${variant} button label`,
            foreground: set.foreground,
            background: set.background,
            min: text,
          } satisfies ContrastPair,
        ],
  ),
  {
    name: 'disabled button label',
    foreground: buttonDisabled.foreground,
    background: buttonDisabled.background,
    min: text,
  },
  ...Object.entries(badgeTones).map(([tone, set]): ContrastPair => ({
    name: `${tone} badge`,
    foreground: set.foreground,
    background: set.background,
    min: text,
  })),
  {
    name: 'input text',
    foreground: inputColors.text,
    background: inputColors.background,
    min: text,
  },
  {
    name: 'input placeholder',
    foreground: inputColors.placeholder,
    background: inputColors.background,
    min: text,
  },
  ...onSurfaces('input border', inputColors.border, nonText),
  ...onSurfaces('focus ring', inputColors.focusBorder, nonText),
  ...onSurfaces('error border', inputColors.error, nonText),
];

function channel(value: number): number {
  const c = value / 255;
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

function luminance(hex: string): number {
  const match = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
  if (!match) throw new Error(`Not a #RRGGBB colour: ${hex}`);
  const [r, g, b] = match.slice(1).map((part) => channel(parseInt(part, 16)));
  return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!;
}

/** WCAG 2.x contrast ratio between two #RRGGBB colours. */
export function contrastRatio(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi! + 0.05) / (lo! + 0.05);
}

const BASE_FONT_SIZE = 16;

/** px to rem, so web text follows the browser's text-size setting. */
export function rem(px: number): string {
  return `${px / BASE_FONT_SIZE}rem`;
}

function kebab(name: string): string {
  return name.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`);
}

function rgba(hex: string, opacity: number): string {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  return `rgba(${r}, ${g}, ${b}, ${opacity})`;
}

/** CSS custom properties (`--tb-*`) for the web, set once on <body>. */
export function cssVariables(): Record<`--tb-${string}`, string> {
  const vars: Record<`--tb-${string}`, string> = {};
  for (const [name, value] of Object.entries(colors)) vars[`--tb-color-${kebab(name)}`] = value;
  for (const [name, value] of Object.entries(spacing)) vars[`--tb-space-${name}`] = `${value}px`;
  for (const [name, value] of Object.entries(radii)) vars[`--tb-radius-${name}`] = `${value}px`;
  for (const [name, value] of Object.entries(borderWidth))
    vars[`--tb-border-${name}`] = `${value}px`;
  for (const [name, value] of Object.entries(controlHeight))
    vars[`--tb-control-${name}`] = `${value}px`;
  vars['--tb-tap-target'] = `${minTapTarget}px`;
  for (const [name, style] of Object.entries(typography)) {
    vars[`--tb-font-${kebab(name)}-size`] = rem(style.fontSize);
    vars[`--tb-font-${kebab(name)}-line`] = rem(style.lineHeight);
    vars[`--tb-font-${kebab(name)}-weight`] = String(style.fontWeight);
  }
  for (const [name, s] of Object.entries(shadows)) {
    vars[`--tb-shadow-${name}`] = `0 ${s.offsetY}px ${s.blur}px ${rgba(s.color, s.opacity)}`;
  }
  for (const [variant, set] of Object.entries({ ...buttonVariants, disabled: buttonDisabled })) {
    vars[`--tb-button-${variant}-bg`] = set.background;
    vars[`--tb-button-${variant}-fg`] = set.foreground;
    vars[`--tb-button-${variant}-border`] = set.border;
  }
  for (const [tone, set] of Object.entries(badgeTones)) {
    vars[`--tb-badge-${tone}-bg`] = set.background;
    vars[`--tb-badge-${tone}-fg`] = set.foreground;
  }
  for (const [name, value] of Object.entries(inputColors))
    vars[`--tb-input-${kebab(name)}`] = value;
  return vars;
}

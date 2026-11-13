import { fontFamily } from '@taskbeavo/ui-tokens';

/**
 * Android can't pick a weight from one family name, so each Manrope weight is loaded under its
 * own name. If a font fails to load, the system font is used.
 */
export const fonts = {
  regular: fontFamily,
  bold: `${fontFamily}-Bold`,
} as const;

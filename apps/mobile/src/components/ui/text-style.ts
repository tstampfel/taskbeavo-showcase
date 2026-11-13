import type { TypographyToken } from '@taskbeavo/ui-tokens';
import { fontWeights, typography } from '@taskbeavo/ui-tokens';
import type { TextStyle } from 'react-native';
import { fonts } from '../../fonts';

/**
 * A typography token as a React Native text style. Only Manrope Regular and Bold are loaded, so
 * semibold and heavier map to Bold. Sizes are not capped: text follows the system font scale.
 */
export function textStyle(token: TypographyToken): TextStyle {
  const { fontSize, lineHeight, fontWeight } = typography[token];
  return {
    fontFamily: fontWeight >= fontWeights.semibold ? fonts.bold : fonts.regular,
    fontSize,
    lineHeight,
  };
}

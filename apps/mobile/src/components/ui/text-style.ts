import type { TypographyToken } from '@taskbeavo/ui-tokens';
import { fontWeights, typography } from '@taskbeavo/ui-tokens';
import type { StyleProp, TextStyle } from 'react-native';
import { Platform, StyleSheet } from 'react-native';
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

/**
 * The bottom padding iOS text gets on top of its own, in points: invisible, but it keeps the
 * box's height off the pixel grid.
 */
export const TEXT_BOX_ROUNDING = 0.01;

/**
 * `style` with a box that's never shorter than its lines on iOS. React Native measures text,
 * rounds the height to the pixel grid, and Yoga can then round the box a fraction of a point
 * shorter. iOS draws the text in that box and folds a last line that no longer fits into the
 * line above, past the right edge ("Wie eingetragen, bis" without "zu 200 Zeichen.", BEAV-223
 * reviews R4-1, R5-1). A fractional height makes Yoga round the text box up instead
 * (react-native#58970). A workaround: remove it once React Native ships
 * react-native#59793, which draws text at unbounded height like it measures it.
 */
export function withTextBoxRounding(style: StyleProp<TextStyle>): StyleProp<TextStyle> {
  if (Platform.OS !== 'ios') return style;
  const { paddingBottom, paddingVertical, padding } = StyleSheet.flatten(style) ?? {};
  const own = paddingBottom ?? paddingVertical ?? padding ?? 0;
  if (typeof own !== 'number') return style;
  return [style, { paddingBottom: own + TEXT_BOX_ROUNDING }];
}

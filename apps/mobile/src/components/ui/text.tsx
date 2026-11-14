import type { TextProps as NativeTextProps } from 'react-native';
import { Text as NativeText, StyleSheet, useWindowDimensions } from 'react-native';
import { CARD_HEADING_INSET, textOf, wordFitFontScale } from './heading';
import { useHeadingCap } from './text-scale-scope';

/*
 * Body text at the largest text sizes (BEAV-223). Like headings (BEAV-219), a paragraph, a
 * money row or a step label must never break a word in the middle, so each text grows with the
 * user's text size only until its longest word would no longer fit on a line. It never shrinks
 * below the design size (a floor of 1x), and never grows past the headings on its screen
 * (`TextScaleScope`).
 */

/** React Native's default font size, for text whose style sets none. */
const DEFAULT_FONT_SIZE = 14;

export interface TextProps extends NativeTextProps {
  /**
   * The screen width the text doesn't get: paddings, margins and anything beside it on its
   * line. Defaults to text in a card on a screen (`CARD_HEADING_INSET`).
   */
  inset?: number;
  /** The width of the text's line, where it isn't the window's less an inset: a table cell. */
  lineWidth?: number;
}

/**
 * The text scale cap `Text` sets for `text` in `style`, for a layout that must match the text's
 * size, such as a mark beside its first line. The rendered scale is the smaller of this and the
 * user's font scale.
 */
export function useTextScaleCap(
  text: string,
  style: TextProps['style'],
  { inset = CARD_HEADING_INSET, lineWidth }: Pick<TextProps, 'inset' | 'lineWidth'> = {},
): number {
  const { width } = useWindowDimensions();
  const fontSize = StyleSheet.flatten(style)?.fontSize ?? DEFAULT_FONT_SIZE;
  const headings = useHeadingCap(fontSize);
  return wordFitFontScale(text, fontSize, lineWidth ?? width - inset, headings);
}

/**
 * The app's text: React Native's Text, but its long words wrap at word boundaries at every
 * text size, and it never grows past the headings on its screen. Use it for all body text;
 * headings use `Heading`.
 */
export function Text({
  inset = CARD_HEADING_INSET,
  lineWidth,
  maxFontSizeMultiplier,
  ...props
}: TextProps) {
  const cap = useTextScaleCap(textOf(props.children), props.style, { inset, lineWidth });
  return (
    <NativeText
      maxFontSizeMultiplier={Math.max(1, Math.min(cap, maxFontSizeMultiplier ?? Infinity))}
      {...props}
    />
  );
}

import { Children, createContext, isValidElement, useContext, type ReactNode } from 'react';
import type { TextProps as NativeTextProps } from 'react-native';
import { Text as NativeText, StyleSheet, useWindowDimensions } from 'react-native';
import {
  CARD_HEADING_INSET,
  isRunningText,
  textOf,
  textScaleFloor,
  withoutAddresses,
  wordFitFontScale,
} from './heading';
import { useHeadingCap } from './text-scale-scope';

/*
 * Body text at the largest text sizes (BEAV-223). Like headings (BEAV-219), a paragraph, a
 * money row or a step label should not break a word in the middle, so each text grows with the
 * user's text size until its longest word would no longer fit on a line. Running text never
 * stops below the user's own size up to 2x (`textScaleFloor`): past that floor a word too long
 * for the line may break rather than hold a whole sentence small, and email and web addresses
 * never set the cap. A label (fewer than `RUNNING_TEXT_MIN_WORDS` words, or `keepWords`) has no
 * floor and keeps its words whole (review R2-1). It never grows past the headings on its screen
 * (`TextScaleScope`).
 */

/** React Native's default font size, for text whose style sets none. */
const DEFAULT_FONT_SIZE = 14;

/** Whether a ui Text is inside another one, as a link or a mark inside a sentence. */
const NestedText = createContext(false);

export interface TextProps extends NativeTextProps {
  /**
   * The screen width the text doesn't get: paddings, margins and anything beside it on its
   * line. Defaults to text in a card on a screen (`CARD_HEADING_INSET`).
   */
  inset?: number;
  /**
   * The width of the text's line, where it isn't the window's less an inset: a table cell. A
   * fixed cell can't take a word at any size, so its text has no floor.
   */
  lineWidth?: number;
  /**
   * Keep every word whole: no 2x floor, even for a long text. Labels of fewer than
   * `RUNNING_TEXT_MIN_WORDS` words keep their words without it.
   */
  keepWords?: boolean;
}

/**
 * The text scale cap for `text` at `fontSize` on a line `width` wide, at the user's `fontScale`,
 * under the `headings` cap of its scope. `floored` is false for fixed cells and `keepWords`.
 */
export function textScaleCap(
  text: string,
  fontSize: number,
  width: number,
  fontScale: number,
  { headings = Infinity, floored = true }: { headings?: number; floored?: boolean } = {},
): number {
  const fit = wordFitFontScale(withoutAddresses(text), fontSize, width);
  const floor = floored && isRunningText(text) ? textScaleFloor(fontScale, fontSize) : 1;
  return Math.max(1, Math.min(Math.max(fit, floor), headings));
}

/**
 * The text scale cap `Text` sets for `text` in `style`, for a layout that must match the text's
 * size, such as a mark beside its first line. The rendered scale is the smaller of this and the
 * user's font scale.
 */
export function useTextScaleCap(
  text: string,
  style: TextProps['style'],
  {
    inset = CARD_HEADING_INSET,
    lineWidth,
    keepWords = false,
  }: Pick<TextProps, 'inset' | 'lineWidth' | 'keepWords'> = {},
): number {
  const { width, fontScale } = useWindowDimensions();
  const fontSize = StyleSheet.flatten(style)?.fontSize ?? DEFAULT_FONT_SIZE;
  const headings = useHeadingCap(fontSize);
  return textScaleCap(text, fontSize, lineWidth ?? width - inset, fontScale, {
    headings,
    floored: lineWidth === undefined && !keepWords,
  });
}

/**
 * The app's text: React Native's Text, but its long words wrap at word boundaries at every
 * text size, and it never grows past the headings on its screen. Use it for all body text;
 * headings use `Heading`. A Text inside another (a link in a sentence) sets no cap of its own:
 * it takes the sentence's size, as one sentence has one size.
 */
export function Text({
  inset = CARD_HEADING_INSET,
  lineWidth,
  keepWords,
  maxFontSizeMultiplier,
  children,
  ...props
}: TextProps) {
  const nested = useContext(NestedText);
  const cap = useTextScaleCap(textOf(children), props.style, { inset, lineWidth, keepWords });
  if (nested) {
    return (
      <NativeText maxFontSizeMultiplier={maxFontSizeMultiplier} {...props}>
        {children}
      </NativeText>
    );
  }
  return (
    <NativeText
      maxFontSizeMultiplier={Math.max(1, Math.min(cap, maxFontSizeMultiplier ?? Infinity))}
      {...props}
    >
      {hasElements(children) ? (
        <NestedText.Provider value>{children}</NestedText.Provider>
      ) : (
        children
      )}
    </NativeText>
  );
}

/** Whether `children` holds elements (a nested Text), not only strings and numbers. */
function hasElements(children: ReactNode): boolean {
  return Children.toArray(children).some(isValidElement);
}

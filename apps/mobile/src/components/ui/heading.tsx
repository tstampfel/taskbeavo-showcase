import { spacing } from '@taskbeavo/ui-tokens';
import type { ReactNode } from 'react';
import { Children, isValidElement } from 'react';
import type { TextProps } from 'react-native';
import { StyleSheet, Text, useWindowDimensions } from 'react-native';

/*
 * Why headings need this: iOS and Android break a word in the middle when the word alone is
 * wider than the line, and at the largest accessibility text sizes a bold heading word such as
 * "cancelling" or "Stornierungsbedingungen" is (BEAV-219). Text that can't fit a word must not
 * grow past the size where the word still fits, so each heading caps its own text scale there.
 * Short headings keep growing with the user's text size; body text is untouched.
 */

/**
 * Upper bounds of Manrope Bold advance widths, in em, by character class. Measured from
 * Manrope_700Bold.ttf (the test checks every character in the catalogues against the font):
 * lowercase letters are at most 0.625 em apart from m and w, capitals and digits at most
 * 0.75 em apart from M and W. Kerning only narrows text, so a sum of these is never narrower
 * than the rendered word.
 */
const NARROW = new Set("iljIı.,:;'’!|".split(''));
const SLIM = new Set('frt1()[]-‐/"„“”'.split(''));
const WIDE = new Set('mwMWœæŒÆ@%—→#'.split(''));
const CAPITAL_LIKE = new Set('=…€_'.split(''));
const NARROW_EM = 0.35;
const SLIM_EM = 0.46;
const WIDE_EM = 1.02;
const CAPITAL_EM = 0.76;
const OTHER_EM = 0.64;

/** Room for rounding and the platform's line fragment padding. */
const SAFETY = 0.96;

/** The width of one character in em, never less than Manrope Bold draws it. */
export function charEm(char: string): number {
  // "é" counts as "e", "Č" as "C": accents don't widen a letter.
  const base = char.normalize('NFD').charAt(0);
  if (NARROW.has(base)) return NARROW_EM;
  if (SLIM.has(base)) return SLIM_EM;
  if (WIDE.has(base)) return WIDE_EM;
  if (CAPITAL_LIKE.has(base) || /\d/.test(base) || base !== base.toLowerCase()) {
    return CAPITAL_EM;
  }
  return OTHER_EM;
}

/**
 * The pieces the platforms may not break inside: runs between spaces (a no-break space joins
 * its neighbours), with a hyphen kept on the piece before it, where a line may end.
 */
export function unbreakableRuns(text: string): string[] {
  return text
    .split(/[ \t\n\r\u2009\u200B]+/)
    .flatMap((word) => word.replace(/([-\u2010])/g, '$1 ').split(' '))
    .filter(Boolean);
}

/** The estimated width, in em, of the widest piece of `text` that can't be broken. */
export function longestRunEm(text: string): number {
  let longest = 0;
  for (const run of unbreakableRuns(text)) {
    let em = 0;
    for (const char of run) em += charEm(char);
    longest = Math.max(longest, em);
  }
  return longest;
}

/**
 * The largest text scale at which every word of `text`, set at `fontSize`, fits in `width`, for
 * `maxFontSizeMultiplier`. Never below 1: at the design size the layouts fit their words, and
 * capping below it would shrink text for everyone. `maxScale` keeps a stricter cap a layout
 * already has, such as the narrow top bar.
 */
export function wordFitFontScale(
  text: string,
  fontSize: number,
  width: number,
  maxScale = Infinity,
): number {
  const em = longestRunEm(text);
  const fit = em > 0 ? (width * SAFETY) / (em * fontSize) : Infinity;
  return Math.max(1, Math.min(fit, maxScale));
}

/** The plain text inside a heading, including nested Text children. */
export function textOf(children: ReactNode): string {
  return Children.toArray(children)
    .map((child) => {
      if (typeof child === 'string' || typeof child === 'number') return String(child);
      if (isValidElement<{ children?: ReactNode }>(child)) return textOf(child.props.children);
      return '';
    })
    .join('');
}

/** React Native's default font size, for a heading whose style sets none. */
const DEFAULT_FONT_SIZE = 14;

/**
 * Horizontal space beside a heading in a card on a screen: the screen body's padding and the
 * card's, on both sides. A heading straight in the body gets a little less room than it has.
 */
export const CARD_HEADING_INSET = 4 * spacing.lg;

export interface HeadingProps extends Omit<
  TextProps,
  'accessibilityRole' | 'maxFontSizeMultiplier'
> {
  /**
   * The screen width the heading doesn't get: paddings, margins and anything beside it on its
   * line. Defaults to a card on a screen (`CARD_HEADING_INSET`).
   */
  inset?: number;
  /** A stricter text scale cap the layout needs anyway, such as a narrow top bar's. */
  maxScale?: number;
  children: ReactNode;
}

/**
 * A heading (`accessibilityRole="header"`) whose long words wrap at word boundaries at every
 * text size: it grows with the user's text size until its longest word would no longer fit on
 * a line, and stops there.
 */
export function Heading({
  inset = CARD_HEADING_INSET,
  maxScale,
  style,
  children,
  ...props
}: HeadingProps) {
  const { width } = useWindowDimensions();
  const fontSize = StyleSheet.flatten(style)?.fontSize ?? DEFAULT_FONT_SIZE;
  return (
    <Text
      accessibilityRole="header"
      maxFontSizeMultiplier={wordFitFontScale(textOf(children), fontSize, width - inset, maxScale)}
      style={style}
      {...props}
    >
      {children}
    </Text>
  );
}

import { spacing, typography } from '@taskbeavo/ui-tokens';
import type { ReactNode } from 'react';
import { Children, isValidElement } from 'react';
import type { TextProps } from 'react-native';
import { StyleSheet, Text, useWindowDimensions } from 'react-native';
import { useRegisterHeading } from './text-scale-scope';

/*
 * Why headings need this: iOS and Android break a word in the middle when the word alone is
 * wider than the line, and at the largest accessibility text sizes a bold heading word such as
 * "cancelling" or "Stornierungsbedingungen" is (BEAV-219). Text that can't fit a word must not
 * grow past the size where the word still fits, so each heading caps its own text scale there.
 * Short headings keep growing with the user's text size; body text follows the same rule
 * (`Text`, BEAV-223).
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

/** Whether every word of `text`, rendered at `fontSize` points, fits in `width`. */
export function wordsFit(text: string, fontSize: number, width: number): boolean {
  return longestRunEm(text) * fontSize <= width * SAFETY;
}

/** The estimated width, in em, of `text` set on one line: never narrower than it renders. */
export function lineEm(text: string): number {
  let em = 0;
  for (const char of text) em += /\s/.test(char) ? NARROW_EM : charEm(char);
  return em;
}

/**
 * Whether texts set side by side on one line, at `fontScale`, with `extra` points of padding
 * and gaps, fit in `width`. Used to decide between a row and a stack before layout, because
 * a flex row squeezes text to a sliver rather than wrapping it on Android.
 */
export function fitsOnOneLine(
  parts: readonly { text: string; fontSize: number }[],
  extra: number,
  width: number,
  fontScale: number,
): boolean {
  let needed = extra;
  for (const { text, fontSize } of parts) needed += lineEm(text) * fontSize * fontScale;
  return needed <= width * SAFETY;
}

/** The text scale body text always reaches when the user's is at least this large (R1-2). */
export const FLOOR_FONT_SCALE = 2;

/**
 * The smallest scale text of `fontSize` is capped at: the user's own scale up to 2x, as WCAG
 * 1.4.4 asks for 200%. Text bigger than body text by design gets the same size in points as
 * body text at that scale, so a heading stays at least as large as the body beside it. Above
 * it, a word too long for the line may break rather than hold the whole text small (BEAV-223).
 */
export function textScaleFloor(fontScale: number, fontSize: number): number {
  const bodyScale = Math.min(fontScale, FLOOR_FONT_SCALE);
  return Math.max(1, bodyScale * Math.min(1, typography.body.fontSize / fontSize));
}

/**
 * `text` without email addresses and web addresses: they aren't words, the platforms wrap them
 * anywhere, and one of them must not hold a whole sentence small.
 */
export function withoutAddresses(text: string): string {
  return text.replace(/\S*(?:@|:\/\/|www\.)\S*/g, ' ');
}

/** The fewest words, not counting addresses, that make a text running text rather than a label. */
export const RUNNING_TEXT_MIN_WORDS = 5;

/**
 * Whether `text` is running text, a sentence that wraps over lines, rather than a label such as
 * a row title, a button or a badge. Only running text gets the 2x floor: a label keeps its
 * words whole, because "Datenschutzerkläru / ng" reads worse than a title a little smaller
 * (BEAV-223 review R2-1).
 */
export function isRunningText(text: string): boolean {
  return withoutAddresses(text).split(/\s+/).filter(Boolean).length >= RUNNING_TEXT_MIN_WORDS;
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
  /**
   * A label, such as a section label: it grows only as far as its words fit, without the floor.
   * It doesn't hold the text in its scope small: that still gets its floor.
   */
  keepWords?: boolean;
  children: ReactNode;
}

/**
 * A heading (`accessibilityRole="header"`) whose long words wrap at word boundaries at every
 * text size: it grows with the user's text size until its longest word would no longer fit on
 * a line, and stops there. Body text in the same `TextScaleScope` grows no further than it
 * (BEAV-223).
 */
export function Heading({
  inset = CARD_HEADING_INSET,
  maxScale,
  keepWords = false,
  style,
  children,
  ...props
}: HeadingProps) {
  const { width, fontScale } = useWindowDimensions();
  const fontSize = StyleSheet.flatten(style)?.fontSize ?? DEFAULT_FONT_SIZE;
  // As far as its words fit, but never below the floor, so body text beside it, which has the
  // same floor, never outgrows it. `maxScale` is a layout's hard cap and wins over the floor.
  const fit = wordFitFontScale(textOf(children), fontSize, width - inset);
  const floor = textScaleFloor(fontScale, fontSize);
  const scale = Math.max(1, Math.min(keepWords ? fit : Math.max(fit, floor), maxScale ?? Infinity));
  useRegisterHeading(keepWords ? Math.max(scale, floor) : scale, fontSize);
  return (
    <Text accessibilityRole="header" maxFontSizeMultiplier={scale} style={style} {...props}>
      {children}
    </Text>
  );
}

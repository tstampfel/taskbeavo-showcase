import { borderWidth, colors, minTapTarget, radii, spacing } from '@taskbeavo/ui-tokens';
import type { ReactNode } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View, useWindowDimensions } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { textStyle } from './text-style';

/**
 * The largest text scale for the top bar title: beyond it long German and French words break
 * mid-word in the narrow bar (as on the sign-in pages, BEAV-190 QA F9). Body text keeps the
 * full scale.
 */
export const TOP_TITLE_MAX_FONT_SCALE = 1.6;

/**
 * From this text scale on, the footer scrolls with the body instead of staying pinned: at the
 * largest accessibility sizes pinned buttons would cover most of the screen.
 */
export const INLINE_FOOTER_FONT_SCALE = 1.5;

export interface ScreenProps {
  /** The top bar title, already translated; it is the screen's heading for screen readers. */
  title: string;
  /** Shows the round back button on the left of the top bar. */
  onBack?: () => void;
  /** The back button's accessibility label, already translated. */
  backLabel?: string;
  /** Pinned under the scrolling body: the screen's main actions (the mockups' `.foot`). */
  footer?: ReactNode;
  children: ReactNode;
  testID?: string;
}

/**
 * A screen from the mockups' phone frames: the top bar (`.top`, a round back button and a
 * centred title), the scrolling body (`.body`) and the actions pinned at the bottom (`.foot`).
 * The SafeAreaView keeps the bar clear of the status bar and notch (BEAV-201) and the footer
 * clear of the home indicator.
 */
export function Screen({ title, onBack, backLabel, footer, children, testID }: ScreenProps) {
  const inline = useWindowDimensions().fontScale >= INLINE_FOOTER_FONT_SCALE;
  const foot = footer ? (
    <View
      style={[styles.foot, inline && styles.footInline]}
      testID={testID ? `${testID}-footer` : undefined}
    >
      {footer}
    </View>
  ) : null;
  return (
    <SafeAreaView style={styles.safeArea} testID={testID}>
      <TopBar title={title} onBack={onBack} backLabel={backLabel} />
      <ScrollView contentContainerStyle={styles.body}>
        {children}
        {inline ? foot : null}
      </ScrollView>
      {inline ? null : foot}
    </SafeAreaView>
  );
}

/** The mockups' `.top` bar on its own, for a screen with its own layout below it. */
export function TopBar({
  title,
  onBack,
  backLabel,
}: Pick<ScreenProps, 'title' | 'onBack' | 'backLabel'>) {
  return (
    <View style={styles.top}>
      {onBack ? (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={backLabel}
          onPress={onBack}
          hitSlop={spacing.xs}
          style={({ pressed }) => [styles.iconButton, styles.round, pressed && styles.pressed]}
          testID="screen-back"
        >
          {/* Drawn, not a "‹" glyph: iOS listed a glyph as its own element beside the
              labelled button, and a drawing stays the same size at large text. */}
          <View style={styles.chevron} />
        </Pressable>
      ) : (
        <View style={styles.iconButton} />
      )}
      <Text
        accessibilityRole="header"
        style={styles.title}
        maxFontSizeMultiplier={TOP_TITLE_MAX_FONT_SCALE}
      >
        {title}
      </Text>
      <View style={styles.iconButton} />
    </View>
  );
}

const CHEVRON_SIZE = 11;
const CHEVRON_STROKE = 2.5;

const styles = StyleSheet.create({
  safeArea: { flex: 1, backgroundColor: colors.offWhite },
  top: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.xs,
    paddingBottom: spacing.sm,
  },
  iconButton: {
    width: minTapTarget,
    height: minTapTarget,
    alignItems: 'center',
    justifyContent: 'center',
  },
  round: {
    borderRadius: radii.pill,
    borderWidth: borderWidth.thin,
    borderColor: colors.line,
    backgroundColor: colors.white,
  },
  pressed: { opacity: 0.85 },
  // A left chevron: two sides of a square, turned 45°, nudged right to look centred.
  chevron: {
    width: CHEVRON_SIZE,
    height: CHEVRON_SIZE,
    marginLeft: CHEVRON_SIZE / 3,
    borderLeftWidth: CHEVRON_STROKE,
    borderBottomWidth: CHEVRON_STROKE,
    borderColor: colors.ink,
    transform: [{ rotate: '45deg' }],
  },
  title: { ...textStyle('button'), flex: 1, textAlign: 'center', color: colors.ink },
  body: { flexGrow: 1, gap: spacing.lg, paddingHorizontal: spacing.lg, paddingBottom: spacing.lg },
  foot: {
    gap: spacing.sm,
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.md,
    paddingBottom: spacing.sm,
  },
  footInline: { paddingHorizontal: 0, paddingBottom: 0 },
});

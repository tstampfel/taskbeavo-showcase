import { borderWidth, colors, minTapTarget, radii, spacing } from '@taskbeavo/ui-tokens';
import type { ReactNode } from 'react';
import {
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  View,
  useWindowDimensions,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Heading } from './heading';
import { TextScaleScope } from './text-scale-scope';
import { textStyle } from './text-style';

/**
 * The largest text scale for the top bar title: beyond it long German and French words break
 * mid-word in the narrow bar (as on the sign-in pages, BEAV-190 QA F9). Body text keeps the
 * full scale.
 */
export const TOP_TITLE_MAX_FONT_SCALE = 1.6;

/** The width beside the top bar title: the bar's padding, the two buttons and their gaps. */
export const TOP_TITLE_INSET = 2 * spacing.lg + 2 * minTapTarget + 2 * spacing.sm;

/**
 * From this text scale on, the footer scrolls with the body instead of staying pinned: at the
 * largest accessibility sizes pinned buttons would cover most of the screen.
 */
export const INLINE_FOOTER_FONT_SCALE = 1.5;

export interface ScreenProps {
  /**
   * The top bar title, already translated; it is the screen's heading for screen readers.
   * Without a title or a back button there is no top bar (the mockups' welcome and terms
   * update screens, 21b).
   */
  title?: string;
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
 * clear of the home indicator. On iOS the footer rides above the keyboard, and a tap on a
 * button works while the keyboard is up (the sign-in forms, BEAV-215). The body is one
 * `TextScaleScope`: its text grows no bigger than its headings (BEAV-223); the top bar title,
 * capped for its narrow bar, is left out.
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
  const bar = Boolean(title || onBack);
  return (
    <SafeAreaView style={styles.safeArea} testID={testID}>
      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        {bar ? <TopBar title={title ?? ''} onBack={onBack} backLabel={backLabel} /> : null}
        <ScrollView
          contentContainerStyle={[styles.body, !bar && styles.bodyWithoutBar]}
          keyboardShouldPersistTaps="handled"
          testID={testID ? `${testID}-body` : undefined}
        >
          <TextScaleScope>
            {children}
            {inline ? foot : null}
          </TextScaleScope>
        </ScrollView>
        {inline ? null : foot}
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

/** The mockups' `.top` bar on its own, for a screen with its own layout below it. */
export function TopBar({
  title,
  onBack,
  backLabel,
}: { title: string } & Pick<ScreenProps, 'onBack' | 'backLabel'>) {
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
      <Heading style={styles.title} inset={TOP_TITLE_INSET} maxScale={TOP_TITLE_MAX_FONT_SCALE}>
        {title}
      </Heading>
      <View style={styles.iconButton} />
    </View>
  );
}

const CHEVRON_SIZE = 11;
const CHEVRON_STROKE = 2.5;

const styles = StyleSheet.create({
  safeArea: { flex: 1, backgroundColor: colors.offWhite },
  flex: { flex: 1 },
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
  // The mockups' screens without a top bar start their body 20pt down (21b).
  bodyWithoutBar: { paddingTop: spacing.xl },
  foot: {
    gap: spacing.sm,
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.md,
    paddingBottom: spacing.sm,
  },
  footInline: { paddingHorizontal: 0, paddingBottom: 0 },
});

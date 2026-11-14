import type { ButtonVariant } from '@taskbeavo/ui-tokens';
import {
  borderWidth,
  buttonDisabled,
  buttonVariants,
  controlHeight,
  minTapTarget,
  spacing,
} from '@taskbeavo/ui-tokens';
import type { PressableProps } from 'react-native';
import { Pressable, StyleSheet, Text, useWindowDimensions } from 'react-native';
import { CARD_HEADING_INSET, wordFitFontScale } from './heading';
import { textStyle } from './text-style';

export interface ButtonProps extends Omit<PressableProps, 'children' | 'style'> {
  /** Visible label, already translated; also the accessibility label unless one is given. */
  label: string;
  variant?: ButtonVariant;
  /** `sm` is still a 44pt tap target. */
  size?: 'md' | 'sm';
  /** Set while the action is in flight; the button is disabled and announced as busy. */
  busy?: boolean;
  /**
   * The screen width outside the button: paddings, margins and anything beside it on its line.
   * Caps the label's text scale where its longest word still fits, as for headings. Defaults
   * to a button in a card on a screen.
   */
  inset?: number;
}

/** Pill button from the mockups' `.btn`. */
export function Button({
  label,
  variant = 'primary',
  size = 'md',
  busy = false,
  inset = CARD_HEADING_INSET,
  disabled,
  accessibilityLabel,
  ...props
}: ButtonProps) {
  const inactive = Boolean(disabled) || busy;
  const set = inactive ? buttonDisabled : buttonVariants[variant];
  const text = size === 'sm' ? textStyle('small') : textStyle('button');
  const padding = (size === 'sm' ? styles.sm : styles.md).paddingHorizontal;
  // Without the cap a long word ("Einstellungen") breaks mid-word at the largest text sizes.
  const labelWidth = useWindowDimensions().width - inset - 2 * (padding + borderWidth.control);
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel ?? label}
      accessibilityState={{ disabled: inactive, busy }}
      disabled={inactive}
      style={({ pressed }) => [
        styles.base,
        size === 'sm' ? styles.sm : styles.md,
        { backgroundColor: set.background, borderColor: set.border },
        pressed && styles.pressed,
      ]}
      {...props}
    >
      <Text
        style={[text, styles.label, { color: set.foreground }]}
        maxFontSizeMultiplier={wordFitFontScale(label, text.fontSize ?? 0, labelWidth)}
      >
        {label}
      </Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  base: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    minWidth: minTapTarget,
    borderWidth: borderWidth.control,
  },
  // A pill at one line. The radius is half the minimum height, not radii.pill: a label that
  // wraps at large text sizes makes the button taller, and a full pill radius would turn it
  // into an oval whose curve cuts into the first and last lines (BEAV-219).
  md: {
    minHeight: controlHeight.md,
    borderRadius: controlHeight.md / 2,
    paddingHorizontal: spacing.xl,
    paddingVertical: spacing.md,
  },
  sm: {
    minHeight: controlHeight.sm,
    borderRadius: controlHeight.sm / 2,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm,
  },
  pressed: { opacity: 0.85 },
  // No numberOfLines: long German and French labels wrap instead of truncating.
  label: { textAlign: 'center', flexShrink: 1 },
});

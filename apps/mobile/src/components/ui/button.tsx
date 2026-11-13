import type { ButtonVariant } from '@taskbeavo/ui-tokens';
import {
  borderWidth,
  buttonDisabled,
  buttonVariants,
  controlHeight,
  minTapTarget,
  radii,
  spacing,
} from '@taskbeavo/ui-tokens';
import type { PressableProps } from 'react-native';
import { Pressable, StyleSheet, Text } from 'react-native';
import { textStyle } from './text-style';

export interface ButtonProps extends Omit<PressableProps, 'children' | 'style'> {
  /** Visible label, already translated; also the accessibility label unless one is given. */
  label: string;
  variant?: ButtonVariant;
  /** `sm` is still a 44pt tap target. */
  size?: 'md' | 'sm';
  /** Set while the action is in flight; the button is disabled and announced as busy. */
  busy?: boolean;
}

/** Pill button from the mockups' `.btn`. */
export function Button({
  label,
  variant = 'primary',
  size = 'md',
  busy = false,
  disabled,
  accessibilityLabel,
  ...props
}: ButtonProps) {
  const inactive = Boolean(disabled) || busy;
  const set = inactive ? buttonDisabled : buttonVariants[variant];
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
        style={[
          size === 'sm' ? textStyle('small') : textStyle('button'),
          styles.label,
          { color: set.foreground },
        ]}
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
    borderRadius: radii.pill,
  },
  md: { minHeight: controlHeight.md, paddingHorizontal: spacing.xl, paddingVertical: spacing.md },
  sm: { minHeight: controlHeight.sm, paddingHorizontal: spacing.lg, paddingVertical: spacing.sm },
  pressed: { opacity: 0.85 },
  // No numberOfLines: long German and French labels wrap instead of truncating.
  label: { textAlign: 'center', flexShrink: 1 },
});

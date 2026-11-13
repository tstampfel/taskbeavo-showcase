import { borderWidth, colors, radii, shadows, spacing, surfaces } from '@taskbeavo/ui-tokens';
import type { ViewProps } from 'react-native';
import { StyleSheet, View } from 'react-native';

export interface CardProps extends ViewProps {
  /** `raised` has a soft shadow; `flat` an outline, for cards inside cards or sheets. */
  variant?: 'raised' | 'flat';
  selected?: boolean;
}

/** White content card from the mockups' `.card`. */
export function Card({ variant = 'raised', selected = false, style, ...props }: CardProps) {
  return (
    <View
      style={[
        styles.card,
        variant === 'raised' ? styles.raised : styles.flat,
        selected && styles.selected,
        style,
      ]}
      {...props}
    />
  );
}

const styles = StyleSheet.create({
  card: {
    gap: spacing.md,
    padding: spacing.lg,
    borderRadius: radii.lg,
    backgroundColor: surfaces.card,
  },
  raised: {
    shadowColor: shadows.card.color,
    shadowOpacity: shadows.card.opacity,
    shadowOffset: { width: 0, height: shadows.card.offsetY },
    shadowRadius: shadows.card.blur / 2,
    elevation: 2,
  },
  flat: { borderWidth: borderWidth.thin, borderColor: colors.line },
  selected: { borderWidth: borderWidth.focus, borderColor: colors.green },
});

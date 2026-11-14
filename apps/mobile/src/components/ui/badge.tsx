import type { BadgeTone } from '@taskbeavo/ui-tokens';
import { badgeTones, spacing } from '@taskbeavo/ui-tokens';
import { StyleSheet, View } from 'react-native';
import { Text } from './text';
import { textStyle } from './text-style';

/** The padding on both sides of a badge's text, together. */
export const BADGE_PADDING = 2 * spacing.sm;

export interface BadgeProps {
  /** Visible text, already translated. */
  label: string;
  tone?: BadgeTone;
  testID?: string;
}

/** Status pill from the mockups' `.badge`. Not interactive, so it has no tap target. */
export function Badge({ label, tone = 'grey', testID }: BadgeProps) {
  const set = badgeTones[tone];
  return (
    <View testID={testID} style={[styles.badge, { backgroundColor: set.background }]}>
      <Text style={[textStyle('caption'), { color: set.foreground }]}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  badge: {
    alignSelf: 'flex-start',
    minHeight: spacing.xl,
    justifyContent: 'center',
    paddingHorizontal: BADGE_PADDING / 2,
    // A pill at one line; half the minimum height, not radii.pill, so a label that wraps at
    // large text keeps square-ish ends instead of an oval that cuts into its lines.
    borderRadius: spacing.xl / 2,
  },
});

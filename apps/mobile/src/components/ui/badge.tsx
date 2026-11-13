import type { BadgeTone } from '@taskbeavo/ui-tokens';
import { badgeTones, radii, spacing } from '@taskbeavo/ui-tokens';
import { StyleSheet, Text, View } from 'react-native';
import { textStyle } from './text-style';

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
    paddingHorizontal: spacing.sm,
    borderRadius: radii.pill,
  },
});

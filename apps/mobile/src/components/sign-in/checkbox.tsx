import { colors, minTapTarget, radii, spacing } from '@taskbeavo/ui-tokens';
import type { ReactNode } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import { CARD_HEADING_INSET } from '../ui/heading';
import { Text } from '../ui/text';
import { textStyle } from '../ui/text-style';

export interface CheckboxProps {
  checked: boolean;
  onChange(checked: boolean): void;
  /** The label, already translated; the whole row is the tap target. */
  label: ReactNode;
  /** Read out instead of `label` when it holds links or other elements. */
  accessibilityLabel?: string;
  testID?: string;
}

/** The mockups' `.check` row (15e): a 24pt box in a row at least 44pt tall. */
export function Checkbox({ checked, onChange, label, accessibilityLabel, testID }: CheckboxProps) {
  return (
    <Pressable
      accessibilityRole="checkbox"
      accessibilityState={{ checked }}
      accessibilityLabel={accessibilityLabel ?? (typeof label === 'string' ? label : undefined)}
      onPress={() => onChange(!checked)}
      style={styles.row}
      testID={testID}
    >
      <View
        style={[styles.box, checked && styles.checked]}
        testID={testID ? `${testID}-box` : undefined}
      >
        {/* Drawn at one size: a growing tick would overflow its 24pt box. */}
        {checked ? (
          <Text style={styles.tick} allowFontScaling={false}>
            ✓
          </Text>
        ) : null}
      </View>
      <View style={styles.label}>
        {typeof label === 'string' ? (
          <Text style={styles.text} inset={CHECKBOX_LABEL_INSET}>
            {label}
          </Text>
        ) : (
          label
        )}
      </View>
    </Pressable>
  );
}

const BOX = 24;
/**
 * The width beside the label: a card's and the screen's padding (the most a checkbox sits in),
 * the box and its gap.
 */
export const CHECKBOX_LABEL_INSET = CARD_HEADING_INSET + BOX + spacing.md;

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'flex-start', gap: spacing.md, minHeight: minTapTarget },
  box: {
    width: BOX,
    height: BOX,
    borderWidth: 2,
    // The mockups' `.check` outline: muted ink, 3:1 or better against white and off-white.
    borderColor: colors.inkMuted,
    flexShrink: 0,
    borderRadius: radii.sm,
    backgroundColor: colors.white,
    alignItems: 'center',
    justifyContent: 'center',
  },
  checked: { backgroundColor: colors.green, borderColor: colors.green },
  tick: { color: colors.white, fontSize: 14, lineHeight: 16 },
  label: { flex: 1 },
  text: { ...textStyle('small'), color: colors.ink },
});

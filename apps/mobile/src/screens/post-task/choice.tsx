import { borderWidth, colors, minTapTarget, radii, spacing } from '@taskbeavo/ui-tokens';
import type { ReactNode } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import { Text } from '../../components/ui/text';
import { textStyle } from '../../components/ui/text-style';

export interface ChoiceProps {
  label: string;
  /** A second line, e.g. a contact's area. */
  detail?: string;
  selected: boolean;
  onPress(): void;
  disabled?: boolean;
  /** `chip` is the mockups' pill; `row` a full-width card row with a radio circle. */
  shape?: 'chip' | 'row';
  accessibilityLabel?: string;
  testID?: string;
}

/** One option of a single choice (the mockups' `.chip` and `.card.sel` with `.radio`). */
export function Choice({
  label,
  detail,
  selected,
  onPress,
  disabled = false,
  shape = 'chip',
  accessibilityLabel,
  testID,
}: ChoiceProps) {
  const row = shape === 'row';
  return (
    <Pressable
      accessibilityRole="radio"
      accessibilityState={{ checked: selected, disabled }}
      accessibilityLabel={accessibilityLabel ?? [label, detail].filter(Boolean).join(', ')}
      disabled={disabled}
      onPress={onPress}
      style={[
        row ? styles.row : styles.chip,
        selected && styles.selected,
        disabled && styles.disabled,
      ]}
      testID={testID}
    >
      {row ? <View style={[styles.radio, selected && styles.radioOn]} /> : null}
      <View style={row ? styles.grow : undefined}>
        <Text
          style={[
            textStyle(row ? 'label' : 'small'),
            selected ? styles.selectedText : disabled ? styles.disabledText : styles.ink,
          ]}
        >
          {label}
        </Text>
        {detail ? (
          <Text style={[textStyle('small'), selected ? styles.selectedText : styles.muted]}>
            {detail}
          </Text>
        ) : null}
      </View>
    </Pressable>
  );
}

/** A wrapping group of choices, announced as one radio group. */
export function ChoiceGroup({ label, children }: { label: string; children: ReactNode }) {
  return (
    <View accessibilityRole="radiogroup" accessibilityLabel={label} style={styles.group}>
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  group: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  chip: {
    minHeight: minTapTarget,
    justifyContent: 'center',
    paddingHorizontal: spacing.md,
    borderRadius: radii.pill,
    borderWidth: borderWidth.thin,
    borderColor: colors.control,
    backgroundColor: colors.white,
  },
  row: {
    width: '100%',
    minHeight: minTapTarget,
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    padding: spacing.md,
    borderRadius: radii.md,
    borderWidth: borderWidth.thin,
    borderColor: colors.control,
    backgroundColor: colors.white,
  },
  selected: { backgroundColor: colors.green, borderColor: colors.green },
  disabled: { backgroundColor: colors.greyTint, borderColor: colors.line },
  selectedText: { color: colors.white },
  // The grey badge pair (ui-tokens contrastPairs).
  disabledText: { color: colors.greyTintText },
  radio: {
    width: 22,
    height: 22,
    flexShrink: 0,
    borderRadius: radii.pill,
    borderWidth: borderWidth.focus,
    borderColor: colors.inkMuted,
    backgroundColor: colors.white,
  },
  radioOn: { borderColor: colors.white, borderWidth: 6 },
  grow: { flex: 1 },
  ink: { color: colors.ink },
  muted: { color: colors.inkMuted },
});

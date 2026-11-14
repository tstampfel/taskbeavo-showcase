import { spacing } from '@taskbeavo/ui-tokens';
import type { StyleProp, TextStyle } from 'react-native';
import { StyleSheet, View, useWindowDimensions } from 'react-native';
import { CARD_HEADING_INSET, fitsOnOneLine } from './heading';
import { Text } from './text';

/** React Native's default font size, for a row whose style sets none. */
const DEFAULT_FONT_SIZE = 14;

export interface AmountRowProps {
  /** What the amount is, already translated. */
  label: string;
  /** The amount, already formatted (formatMoney). Shown exactly as given. */
  value: string;
  /** The text style of both sides. */
  style?: StyleProp<TextStyle>;
  /** Extra style for the amount, such as the green of a refund. */
  valueStyle?: StyleProp<TextStyle>;
  /** The screen width the row doesn't get. Defaults to a row in a card on a screen. */
  inset?: number;
  testID?: string;
}

/**
 * A money row: the label on the left, the amount on the right (the mockups' summary lines).
 * When the two don't fit on one line, as at the largest text sizes in French or German, the
 * amount goes on its own line under the label, still on the right, and the label gets the full
 * width, so its words wrap whole instead of breaking mid-word beside the amount (BEAV-223).
 * Screen readers hear one element, "label: amount".
 */
export function AmountRow({
  label,
  value,
  style,
  valueStyle,
  inset = CARD_HEADING_INSET,
  testID,
}: AmountRowProps) {
  const { width, fontScale } = useWindowDimensions();
  const fontSize = StyleSheet.flatten(style)?.fontSize ?? DEFAULT_FONT_SIZE;
  const row = fitsOnOneLine(
    [
      { text: label, fontSize },
      { text: value, fontSize },
    ],
    spacing.md,
    width - inset,
    fontScale,
  );
  return (
    <View
      style={row ? styles.row : styles.stack}
      accessible
      accessibilityLabel={`${label}: ${value}`}
      testID={testID}
    >
      <Text style={[style, row && styles.label]} inset={inset}>
        {label}
      </Text>
      <Text style={[style, valueStyle, !row && styles.valueUnder]} inset={inset}>
        {value}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', gap: spacing.md },
  stack: { gap: spacing.xs },
  label: { flex: 1 },
  valueUnder: { alignSelf: 'flex-end', textAlign: 'right' },
});

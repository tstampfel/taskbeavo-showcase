import { spacing } from '@taskbeavo/ui-tokens';
import type { ReactNode } from 'react';
import {
  createContext,
  useCallback,
  useContext,
  useId,
  useLayoutEffect,
  useMemo,
  useState,
} from 'react';
import type { StyleProp, TextStyle } from 'react-native';
import { StyleSheet, View, useWindowDimensions } from 'react-native';
import { CARD_HEADING_INSET, fitsOnOneLine } from './heading';
import { Text, useTextScaleCap } from './text';

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

interface Group {
  /** Whether every row of the group fits on one line. */
  allFit: boolean;
  report(id: string, fits: boolean): () => void;
}

const GroupContext = createContext<Group | null>(null);

/**
 * Money rows that are read as one block, such as a cancellation's refund lines or a checkout
 * summary: they are all on one line or all stacked. Deciding row by row made them zigzag where
 * the one-line estimate is close, as at Android's nonlinear font scaling (review R1-3).
 */
export function AmountRowGroup({ children }: { children: ReactNode }) {
  const [fits, setFits] = useState<ReadonlyMap<string, boolean>>(new Map());
  const report = useCallback((id: string, fit: boolean) => {
    setFits((current) => (current.get(id) === fit ? current : new Map(current).set(id, fit)));
    return () =>
      setFits((current) => {
        if (!current.has(id)) return current;
        const next = new Map(current);
        next.delete(id);
        return next;
      });
  }, []);
  const value = useMemo(
    () => ({ allFit: [...fits.values()].every(Boolean), report }),
    [fits, report],
  );
  return <GroupContext.Provider value={value}>{children}</GroupContext.Provider>;
}

/**
 * A money row: the label on the left, the amount on the right (the mockups' summary lines).
 * When the two don't fit on one line, as at the largest text sizes in French or German, the
 * amount goes on its own line under the label, still on the right, and the label gets the full
 * width, so its words wrap whole instead of breaking mid-word beside the amount (BEAV-223).
 * Screen readers hear one element, "label: amount". Inside an `AmountRowGroup` the rows of a
 * block decide together.
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
  // One size for both sides, the smaller of their caps, and the layout decided at that size.
  const cap = Math.min(
    useTextScaleCap(label, style, { inset }),
    useTextScaleCap(value, [style, valueStyle], { inset }),
  );
  const fits = fitsOnOneLine(
    [
      { text: label, fontSize },
      { text: value, fontSize },
    ],
    spacing.md,
    width - inset,
    Math.min(fontScale, cap),
  );
  const group = useContext(GroupContext);
  const id = useId();
  const report = group?.report;
  useLayoutEffect(() => (report ? report(id, fits) : undefined), [report, id, fits]);
  const row = group ? fits && group.allFit : fits;
  return (
    <View
      style={row ? styles.row : styles.stack}
      accessible
      accessibilityLabel={`${label}: ${value}`}
      testID={testID}
    >
      <Text style={[style, row && styles.label]} inset={inset} maxFontSizeMultiplier={cap}>
        {label}
      </Text>
      <Text
        style={[style, valueStyle, !row && styles.valueUnder]}
        inset={inset}
        maxFontSizeMultiplier={cap}
      >
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

import { borderWidth, colors, minTapTarget, spacing } from '@taskbeavo/ui-tokens';
import type { ReactElement, ReactNode } from 'react';
import { Children, cloneElement, isValidElement } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import { Text } from './text';
import { fonts } from '../../fonts';
import { Card } from './card';
import { textStyle } from './text-style';

export interface ListCardProps {
  /** `ListRow`s; each one after the first gets a hairline above it. */
  children: ReactNode;
  testID?: string;
}

/** The mockups' list card (19a, 21d): one white card, its rows split by hairlines. */
export function ListCard({ children, testID }: ListCardProps) {
  let index = 0;
  return (
    <Card style={styles.card} testID={testID}>
      {Children.map(children, (child) => {
        if (!isValidElement(child)) return child;
        const divided = index > 0;
        index += 1;
        return cloneElement(child as ReactElement<ListRowProps>, { divided });
      })}
    </Card>
  );
}

export interface ListRowProps {
  /** The row's text, already translated; also its accessibility label when it is tappable. */
  title: string;
  /** A muted line under the title, already translated. */
  subtitle?: string;
  /** Makes the whole row tappable, with a chevron on the right. */
  onPress?: () => void;
  /** `link` for a row that opens a page in the browser. */
  role?: 'button' | 'link';
  /** Drawn on the right instead of the chevron, such as a switch. */
  trailing?: ReactNode;
  /** Set by `ListCard` for every row after the first. */
  divided?: boolean;
  testID?: string;
}

/** A row of a `ListCard`: a bold title, an optional muted line, a chevron or a control. */
export function ListRow({
  title,
  subtitle,
  onPress,
  role = 'button',
  trailing,
  divided = false,
  testID,
}: ListRowProps) {
  // At the largest text sizes a long word such as "information" would break mid-word; the
  // title grows only as far as its longest word still fits beside the chevron or control, with
  // no 2x floor (review R2-1). A long subtitle is running text and keeps the floor. A row with
  // a chevron gives its title the room the chevron leaves, not a switch's (review R3-2).
  const inset = trailing ? TITLE_INSET : CHEVRON_TITLE_INSET;
  const text = (
    <View style={styles.text}>
      <Text style={styles.title} inset={inset} keepWords>
        {title}
      </Text>
      {subtitle ? (
        <Text style={styles.subtitle} inset={inset}>
          {subtitle}
        </Text>
      ) : null}
    </View>
  );
  if (!onPress) {
    return (
      <View style={[styles.row, divided && styles.divided]} testID={testID}>
        {text}
        {trailing}
      </View>
    );
  }
  return (
    <Pressable
      accessibilityRole={role}
      accessibilityLabel={title}
      accessibilityHint={subtitle}
      onPress={onPress}
      style={({ pressed }) => [styles.row, divided && styles.divided, pressed && styles.pressed]}
      testID={testID}
    >
      {text}
      {trailing ?? (
        <View
          style={styles.chevronBox}
          importantForAccessibility="no-hide-descendants"
          accessibilityElementsHidden
          testID={testID ? `${testID}-chevron` : undefined}
        >
          <View style={styles.chevron} />
        </View>
      )}
    </Pressable>
  );
}

const CHEVRON_SIZE = 9;
/** The widest trailing control, a native switch (51pt on iOS). */
const TRAILING_WIDTH = 52;
/** The width beside a row's title: page and card padding on both sides, the gap, the trailing control. */
export const TITLE_INSET = 4 * spacing.lg + spacing.md + TRAILING_WIDTH;
/** The width beside a row's title when the chevron (its box, `spacing.lg`) is on the right. */
export const CHEVRON_TITLE_INSET = 4 * spacing.lg + spacing.md + spacing.lg;
const CHEVRON_STROKE = 2;

const styles = StyleSheet.create({
  card: { gap: 0, paddingVertical: spacing.xs },
  // The mockups' rows are 52 to 64pt tall; at large text they grow with their text.
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    minHeight: minTapTarget + spacing.md,
    paddingVertical: spacing.sm,
  },
  divided: { borderTopWidth: borderWidth.thin, borderTopColor: colors.line },
  pressed: { opacity: 0.85 },
  text: { flex: 1, gap: 2 },
  // The mockups' `.p` at weight 700: body size, bold.
  title: { ...textStyle('body'), fontFamily: fonts.bold, color: colors.ink },
  subtitle: { ...textStyle('caption'), color: colors.inkMuted },
  chevronBox: { width: spacing.lg, alignItems: 'center', justifyContent: 'center' },
  // A right chevron, drawn like the top bar's back chevron (the mockups' grey `right` icon).
  chevron: {
    width: CHEVRON_SIZE,
    height: CHEVRON_SIZE,
    marginRight: CHEVRON_SIZE / 3,
    borderRightWidth: CHEVRON_STROKE,
    borderTopWidth: CHEVRON_STROKE,
    borderColor: colors.inkMuted,
    transform: [{ rotate: '45deg' }],
  },
});

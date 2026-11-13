import { colors, radii, spacing } from '@taskbeavo/ui-tokens';
import type { ReactNode } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { textStyle } from './text-style';

export type NoteTone = 'green' | 'warn' | 'red';

// The badge tints: each pair is in ui-tokens' contrastPairs (WCAG AA).
const tones = {
  green: { background: colors.greenTint, foreground: colors.green },
  warn: { background: colors.orangeTint, foreground: colors.orangeTintText },
  red: { background: colors.redTint, foreground: colors.redTintText },
} as const;

export interface NoteProps {
  tone?: NoteTone;
  /** Text, already translated, or text nodes for a bold part. */
  children: ReactNode;
  testID?: string;
}

/** The mockups' tinted `.note` box: a short explanation next to an action. */
export function Note({ tone = 'green', children, testID }: NoteProps) {
  const set = tones[tone];
  return (
    <View style={[styles.note, { backgroundColor: set.background }]} testID={testID}>
      <Text style={[textStyle('label'), { color: set.foreground }]}>{children}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  note: { padding: spacing.md, borderRadius: radii.md },
});

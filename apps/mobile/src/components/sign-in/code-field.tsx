import { CODE_LENGTH, normalizeCode } from '@taskbeavo/graphql';
import { borderWidth, colors, inputColors, radii, spacing } from '@taskbeavo/ui-tokens';
import { StyleSheet, TextInput, View } from 'react-native';
import { Text } from '../ui/text';
import { fonts } from '../../fonts';
import { textStyle } from '../ui/text-style';

export interface CodeFieldProps {
  label: string;
  value: string;
  onChange(code: string): void;
  error?: string | null;
  editable?: boolean;
  testID: string;
}

/**
 * The six-digit code entry of mockups 15c, 15d and 15g, as one field so the keyboard can offer
 * the code from the SMS or email (iOS one-time-code, Android SMS autofill).
 */
export function CodeField({
  label,
  value,
  onChange,
  error,
  editable = true,
  testID,
}: CodeFieldProps) {
  return (
    <View style={styles.field}>
      <TextInput
        accessibilityLabel={label}
        accessibilityHint={error ?? undefined}
        autoFocus
        keyboardType="number-pad"
        textContentType="oneTimeCode"
        autoComplete={'one-time-code' as never}
        maxLength={CODE_LENGTH}
        value={value}
        editable={editable}
        onChangeText={(text) => onChange(normalizeCode(text))}
        style={[
          styles.input,
          error ? { borderColor: inputColors.error, borderWidth: borderWidth.focus } : null,
        ]}
        testID={testID}
      />
      {error ? (
        <Text
          accessibilityLiveRegion="polite"
          accessibilityRole="alert"
          style={styles.error}
          testID={`${testID}-error`}
        >
          {error}
        </Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  field: { gap: spacing.sm },
  input: {
    minHeight: 60,
    paddingHorizontal: spacing.lg,
    borderWidth: borderWidth.thin,
    borderColor: inputColors.border,
    borderRadius: radii.md,
    backgroundColor: inputColors.background,
    color: inputColors.text,
    fontFamily: fonts.bold,
    fontSize: 24,
    letterSpacing: 12,
    textAlign: 'center',
  },
  error: { ...textStyle('small'), fontFamily: fonts.bold, color: colors.error },
});

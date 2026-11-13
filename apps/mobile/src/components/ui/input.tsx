import { borderWidth, controlHeight, inputColors, radii, spacing } from '@taskbeavo/ui-tokens';
import { useState } from 'react';
import type { TextInputProps } from 'react-native';
import { StyleSheet, Text, TextInput, View } from 'react-native';
import { textStyle } from './text-style';

export interface InputProps extends Omit<TextInputProps, 'style' | 'placeholderTextColor'> {
  /** Visible label, already translated; also the field's accessibility label. */
  label: string;
  hint?: string;
  /** Translated error message; read out with the field and announced when it appears. */
  error?: string;
}

/** Labelled text field from the mockups' `.input`. */
export function Input({ label, hint, error, onFocus, onBlur, testID, ...props }: InputProps) {
  const [focused, setFocused] = useState(false);
  const borderColor = error
    ? inputColors.error
    : focused
      ? inputColors.focusBorder
      : inputColors.border;
  const thick = Boolean(error) || focused;

  return (
    <View style={styles.field}>
      <Text
        style={[textStyle('label'), { color: inputColors.label }]}
        importantForAccessibility="no"
      >
        {label}
      </Text>
      <TextInput
        accessibilityLabel={label}
        accessibilityHint={[error, hint].filter(Boolean).join('. ') || undefined}
        testID={testID}
        placeholderTextColor={inputColors.placeholder}
        onFocus={(event) => {
          setFocused(true);
          onFocus?.(event);
        }}
        onBlur={(event) => {
          setFocused(false);
          onBlur?.(event);
        }}
        style={[
          textStyle('body'),
          styles.input,
          { borderColor, borderWidth: thick ? borderWidth.focus : borderWidth.thin },
        ]}
        {...props}
      />
      {hint ? <Text style={[textStyle('small'), { color: inputColors.hint }]}>{hint}</Text> : null}
      {error ? (
        <Text
          accessibilityLiveRegion="polite"
          testID={testID ? `${testID}-error` : undefined}
          style={[textStyle('small'), { color: inputColors.error }]}
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
    minHeight: controlHeight.md,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    borderRadius: radii.md,
    backgroundColor: inputColors.background,
    color: inputColors.text,
  },
});

import { countryFlag, countryName, sortedCountries, type Locale } from '@taskbeavo/i18n';
import {
  borderWidth,
  colors,
  controlHeight,
  inputColors,
  minTapTarget,
  radii,
  spacing,
} from '@taskbeavo/ui-tokens';
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { FlatList, Modal, Pressable, StyleSheet, TextInput, View } from 'react-native';
import { Text } from '../ui/text';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Button, Heading } from '../ui';
import { textStyle } from '../ui/text-style';

export interface CountryPickerProps {
  label: string;
  value: string;
  onChange(country: string): void;
  testID: string;
}

/** "Country you live in" (mockup 15e): a field that opens a searchable list of every country. */
export function CountryPicker({ label, value, onChange, testID }: CountryPickerProps) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language as Locale;
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  // The Done button shares the header line; its width grows with the text size.
  const [doneWidth, setDoneWidth] = useState<number>(minTapTarget);
  const countries = useMemo(() => sortedCountries(locale), [locale]);
  const shown = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase(locale);
    return needle
      ? countries.filter(
          (c) =>
            c.name.toLocaleLowerCase(locale).includes(needle) || c.code === needle.toUpperCase(),
        )
      : countries;
  }, [countries, query, locale]);
  const name = countryName(value, locale);

  return (
    <View style={styles.field}>
      <Text
        style={[textStyle('label'), { color: inputColors.label }]}
        importantForAccessibility="no"
      >
        {label}
      </Text>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`${label}: ${name}`}
        onPress={() => setOpen(true)}
        style={styles.control}
        testID={testID}
      >
        <Text style={[textStyle('body'), styles.value]}>{`${countryFlag(value)} ${name}`}</Text>
      </Pressable>
      <Modal visible={open} animationType="slide" onRequestClose={() => setOpen(false)}>
        <SafeAreaView style={styles.sheet}>
          <View style={styles.header}>
            <Heading
              style={[textStyle('h2'), styles.value, styles.title]}
              inset={2 * spacing.lg + doneWidth}
            >
              {label}
            </Heading>
            <Button
              label={t('signIn.countryDone')}
              variant="ghost"
              size="sm"
              onPress={() => setOpen(false)}
              onLayout={(event) => setDoneWidth(event.nativeEvent.layout.width)}
            />
          </View>
          <TextInput
            accessibilityLabel={t('signIn.countrySearch')}
            placeholder={t('signIn.countrySearch')}
            placeholderTextColor={inputColors.placeholder}
            value={query}
            onChangeText={setQuery}
            autoCorrect={false}
            style={[textStyle('body'), styles.search]}
            testID={`${testID}-search`}
          />
          <FlatList
            data={shown}
            keyExtractor={(item) => item.code}
            keyboardShouldPersistTaps="handled"
            renderItem={({ item }) => (
              <Pressable
                accessibilityRole="radio"
                accessibilityState={{ selected: item.code === value, checked: item.code === value }}
                accessibilityLabel={item.name}
                onPress={() => {
                  onChange(item.code);
                  setOpen(false);
                  setQuery('');
                }}
                style={[styles.option, item.code === value && styles.selected]}
                testID={`${testID}-${item.code}`}
              >
                <Text style={[textStyle('body'), styles.value]}>
                  {`${countryFlag(item.code)} ${item.name}`}
                </Text>
              </Pressable>
            )}
          />
        </SafeAreaView>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  field: { gap: spacing.sm },
  control: {
    minHeight: controlHeight.md,
    justifyContent: 'center',
    paddingHorizontal: spacing.lg,
    borderWidth: borderWidth.thin,
    borderColor: inputColors.border,
    borderRadius: radii.md,
    backgroundColor: inputColors.background,
  },
  value: { color: colors.ink },
  sheet: {
    flex: 1,
    backgroundColor: colors.offWhite,
    paddingHorizontal: spacing.lg,
    gap: spacing.md,
  },
  header: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  title: { flexShrink: 1 },
  search: {
    minHeight: controlHeight.md,
    paddingHorizontal: spacing.lg,
    borderWidth: borderWidth.thin,
    borderColor: inputColors.border,
    borderRadius: radii.md,
    backgroundColor: inputColors.background,
    color: inputColors.text,
  },
  option: {
    minHeight: minTapTarget,
    justifyContent: 'center',
    paddingHorizontal: spacing.md,
    borderRadius: radii.md,
  },
  selected: { backgroundColor: colors.greenTint },
});

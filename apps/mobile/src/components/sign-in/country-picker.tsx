import { countryFlag, countryName, sortedCountries, type Locale } from '@taskbeavo/i18n';
import {
  borderWidth,
  colors,
  controlHeight,
  inputColors,
  minTapTarget,
  radii,
  spacing,
  typography,
} from '@taskbeavo/ui-tokens';
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  FlatList,
  Modal,
  Pressable,
  StyleSheet,
  TextInput,
  View,
  useWindowDimensions,
} from 'react-native';
import { Text } from '../ui/text';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { Button, Heading } from '../ui';
import { lineEm, wordFitFontScale } from '../ui/heading';
import { TextScaleScope } from '../ui/text-scale-scope';
import { textStyle } from '../ui/text-style';

/** The code beside each name grows to twice its size at most, so the name keeps its room. */
const CODE_MAX_SCALE = 2;
/** The widest the code can be: two of the widest capitals, at its largest. */
const CODE_WIDTH = lineEm('WW') * typography.small.fontSize * CODE_MAX_SCALE;

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
  const { width } = useWindowDimensions();
  // Once Done grows past a third of the line at large text, the title goes under it on a line
  // of its own, instead of being squeezed into what's left (BEAV-223).
  const titleBelow = doneWidth > (width - 2 * spacing.lg) / 3;
  const countries = useMemo(() => sortedCountries(locale), [locale]);
  // One size for the whole list, so the rows don't jump in size from one name to the next: as
  // large as the longest word of any name still fits its line.
  const listScale = useMemo(
    () =>
      Math.min(
        ...countries.map(({ code, name }) =>
          wordFitFontScale(
            `${countryFlag(code)} ${name}`,
            typography.body.fontSize,
            width - OPTION_INSET,
          ),
        ),
      ),
    [countries, width],
  );
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
        {/* A modal is a new native window: without its own provider the safe area reads as
            zero there, and the Done button sat under the status bar (BEAV-223). */}
        <SafeAreaProvider>
          {/* Its own text: the list grows with this title, not with the form behind it. */}
          <TextScaleScope>
            <SafeAreaView style={styles.sheet}>
              <View style={titleBelow ? styles.headerStacked : styles.header}>
                <Heading
                  style={[
                    textStyle('h2'),
                    styles.value,
                    styles.title,
                    titleBelow && styles.titleStacked,
                  ]}
                  inset={2 * spacing.lg + (titleBelow ? 0 : doneWidth)}
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
                    accessibilityState={{
                      selected: item.code === value,
                      checked: item.code === value,
                    }}
                    accessibilityLabel={item.name}
                    onPress={() => {
                      onChange(item.code);
                      setOpen(false);
                      setQuery('');
                    }}
                    style={[styles.option, item.code === value && styles.selected]}
                    testID={`${testID}-${item.code}`}
                  >
                    <Text
                      style={[textStyle('body'), styles.value, styles.name]}
                      inset={OPTION_INSET}
                      maxFontSizeMultiplier={listScale}
                    >
                      {`${countryFlag(item.code)} ${item.name}`}
                    </Text>
                    <Text
                      style={[textStyle('small'), styles.code]}
                      maxFontSizeMultiplier={CODE_MAX_SCALE}
                      importantForAccessibility="no"
                    >
                      {item.code}
                    </Text>
                  </Pressable>
                )}
              />
            </SafeAreaView>
          </TextScaleScope>
        </SafeAreaProvider>
      </Modal>
    </View>
  );
}

/** The width beside a country's name: the sheet's and the option's padding, the code and gap. */
const OPTION_INSET = 2 * spacing.lg + 2 * spacing.md + CODE_WIDTH + spacing.md;

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
  // Done first, on the right, then the title on the whole line under it.
  headerStacked: { flexDirection: 'column-reverse', alignItems: 'flex-end', gap: spacing.sm },
  titleStacked: { alignSelf: 'stretch' },
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
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    paddingHorizontal: spacing.md,
    borderRadius: radii.md,
  },
  selected: { backgroundColor: colors.greenTint },
  name: { flex: 1 },
  // The ISO code after the name ("HR"), muted: names are the list, codes help find one.
  code: { color: colors.inkMuted },
});

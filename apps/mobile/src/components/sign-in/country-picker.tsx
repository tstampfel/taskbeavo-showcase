import {
  countryFlag,
  countryMatches,
  countryName,
  sortedCountries,
  type Locale,
} from '@taskbeavo/i18n';
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
import { CARD_HEADING_INSET, lineEm, wordFitFontScale } from '../ui/heading';
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
  const { width, fontScale } = useWindowDimensions();
  // The Done button shares the header line; its width grows with the text size. It's estimated
  // before layout, not measured, so the first frame already has the final header (review R4-1).
  const doneWidth = doneButtonWidth(t('signIn.countryDone'), fontScale, width);
  // Once Done grows past a third of the line at large text, the title goes under it on a line
  // of its own, instead of being squeezed into what's left (BEAV-223).
  const titleBelow = doneWidth > (width - 2 * spacing.lg) / 3;
  const countries = useMemo(() => sortedCountries(locale), [locale]);
  const shown = useMemo(
    () => countries.filter((country) => countryMatches(country, query, locale)),
    [countries, query, locale],
  );
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
                  // A new text view when the layout flips, so no line breaks carry over.
                  key={titleBelow ? 'below' : 'beside'}
                  style={[textStyle('h2'), styles.value, styles.title]}
                  inset={2 * spacing.lg + (titleBelow ? 0 : doneWidth)}
                >
                  {label}
                </Heading>
                <View style={titleBelow && styles.doneRow}>
                  <Button
                    label={t('signIn.countryDone')}
                    variant="ghost"
                    size="sm"
                    onPress={() => setOpen(false)}
                  />
                </View>
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
                    {/* Each name as large as its longest word fits, whole (review R2-1): one size
                        for the list would hold every name to "Sonderverwaltungsregion"'s. */}
                    <Text
                      style={[textStyle('body'), styles.value, styles.name]}
                      inset={OPTION_INSET}
                      keepWords
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

/**
 * The estimated width of the header's small ghost Done button at `fontScale`: its label on one
 * line at the scale Button allows it (capped where its longest word fits), plus its padding and
 * border, and never under a tap target. Never narrower than it renders, like `lineEm`.
 */
export function doneButtonWidth(label: string, fontScale: number, screenWidth: number): number {
  const fontSize = typography.small.fontSize;
  const chrome = 2 * (spacing.lg + borderWidth.control);
  const cap = wordFitFontScale(label, fontSize, screenWidth - CARD_HEADING_INSET - chrome);
  const scale = Math.max(1, Math.min(fontScale, cap));
  return Math.max(minTapTarget, lineEm(label) * fontSize * scale + chrome);
}

/** The width beside a country's name: the sheet's and the option's padding, the code and gap. */
export const OPTION_INSET = 2 * spacing.lg + 2 * spacing.md + CODE_WIDTH + spacing.md;

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
  headerStacked: { flexDirection: 'column-reverse', gap: spacing.sm },
  doneRow: { flexDirection: 'row', justifyContent: 'flex-end' },
  // The font's own line height, not h2's fixed one: in this modal, iOS drew a title with a fixed
  // line height on fewer lines than it measured, so "Država u / kojoj živite" ran past the
  // right edge at AX5 on a 390pt phone, above an empty line (review R4-1).
  title: { flexShrink: 1, lineHeight: undefined },
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

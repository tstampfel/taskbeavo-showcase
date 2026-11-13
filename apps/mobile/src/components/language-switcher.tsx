import { languageNames, locales, type Locale } from '@taskbeavo/i18n';
import { colors, minTapTarget, radii, spacing } from '@taskbeavo/ui-tokens';
import { useTranslation } from 'react-i18next';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { fonts } from '../fonts';
import { textStyle } from './ui/text-style';

export interface LanguageSwitcherProps {
  /** Called with the picked language; the app passes `chooseLocale` with `appLanguage`. */
  onChoose(locale: Locale): void;
}

/** HR / EN / DE / FR segmented control from the mockups (15e "App language"). */
export function LanguageSwitcher({ onChoose }: LanguageSwitcherProps) {
  const { t, i18n } = useTranslation();
  const current = i18n.language;

  return (
    <View
      accessibilityRole="radiogroup"
      accessibilityLabel={t('languageSwitcher.label')}
      style={styles.group}
      testID="language-switcher"
    >
      {locales.map((locale) => {
        const selected = locale === current;
        return (
          <Pressable
            key={locale}
            accessibilityRole="radio"
            accessibilityLabel={languageNames[locale]}
            accessibilityLanguage={locale}
            accessibilityState={{ selected, checked: selected }}
            onPress={() => {
              if (!selected) onChoose(locale);
            }}
            style={[styles.option, selected && styles.selected]}
          >
            <Text style={[textStyle('small'), styles.label, selected && styles.selectedLabel]}>
              {locale.toUpperCase()}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  group: {
    flexDirection: 'row',
    alignSelf: 'stretch',
    gap: 2,
    padding: spacing.xs,
    borderRadius: radii.pill,
    backgroundColor: colors.greyTint,
  },
  option: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: minTapTarget,
    minWidth: minTapTarget,
    borderRadius: radii.pill,
  },
  selected: {
    backgroundColor: colors.white,
    shadowColor: colors.ink,
    shadowOpacity: 0.08,
    shadowRadius: 4,
    shadowOffset: { width: 0, height: 1 },
    elevation: 1,
  },
  label: { fontFamily: fonts.bold, color: colors.greyTintText },
  selectedLabel: { color: colors.green },
});

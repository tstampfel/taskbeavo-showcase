import { colors } from '@taskbeavo/ui-tokens';
import { useRouter } from 'expo-router';
import { useTranslation } from 'react-i18next';
import { ScrollView, StyleSheet } from 'react-native';
import { Text } from '../src/components/ui/text';
import { SafeAreaView } from 'react-native-safe-area-context';
import { AccountRow } from '../src/components/account-row';
import { ApiStatus } from '../src/components/api-status';
import { LanguageSwitcher } from '../src/components/language-switcher';
import { TermsBanner } from '../src/components/terms-banner';
import { Button, Heading } from '../src/components/ui';
import { fonts } from '../src/fonts';
import { appLanguage } from '../src/i18n/language';
import { chooseLocale } from '../src/i18n/preference';

/** Placeholder welcome screen (mockup 01-onboarding, "1a Welcome") until onboarding is built. */
export default function Welcome() {
  const { t } = useTranslation();
  const router = useRouter();

  return (
    <SafeAreaView style={styles.safeArea}>
      <ScrollView contentContainerStyle={styles.content}>
        <Text style={styles.appName}>{t('common.appName')}</Text>
        <Heading style={styles.headline}>{t('welcome.headline')}</Heading>
        <Text style={styles.body}>{t('welcome.body')}</Text>
        <Text style={styles.comingSoon}>{t('home.comingSoon')}</Text>
        <TermsBanner />
        <ApiStatus />
        <AccountRow />
        <LanguageSwitcher onChoose={(locale) => void chooseLocale(locale, appLanguage)} />
        <Button
          label={t('settings.title')}
          variant="secondary"
          onPress={() => router.push('/settings')}
          testID="open-settings"
        />
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: colors.offWhite,
  },
  content: {
    flexGrow: 1,
    justifyContent: 'center',
    alignItems: 'center',
    gap: 20,
    padding: 24,
  },
  appName: {
    fontFamily: fonts.bold,
    fontSize: 18,
    color: colors.green,
  },
  headline: {
    fontFamily: fonts.bold,
    fontSize: 34,
    lineHeight: 40,
    textAlign: 'center',
    color: colors.ink,
  },
  body: {
    fontFamily: fonts.regular,
    fontSize: 17,
    lineHeight: 24,
    textAlign: 'center',
    color: colors.ink,
    maxWidth: 320,
  },
  comingSoon: {
    fontFamily: fonts.bold,
    fontSize: 15,
    textAlign: 'center',
    color: colors.green,
  },
});

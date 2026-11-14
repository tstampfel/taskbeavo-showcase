import { LEGAL_PAGE_KINDS, LEGAL_PAGES, type LegalPage } from '@taskbeavo/app-links';
import { geoliteAttributionOrder, type Locale } from '@taskbeavo/i18n';
import { colors, minTapTarget, spacing } from '@taskbeavo/ui-tokens';
import { useTranslation } from 'react-i18next';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { textStyle } from '../components/ui/text-style';
import { Heading } from '../components/ui';
import { styles as rowStyles } from './settings-screen';
import {
  Page,
  PAGE_HEADING_INSET,
  TITLE_MAX_FONT_SCALE,
  styles as pageStyles,
} from './sign-in-screen';
import { Rich } from './terms-update-screen';

export const MAXMIND_URL = 'https://www.maxmind.com';

export interface LegalLicencesScreenProps {
  onBack(): void;
  /** Opens a legal web page in the in-app browser. */
  openLegal(page: LegalPage, locale: Locale): void;
  openUrl(url: string): void;
}

/**
 * Settings → Legal and licences: the legal pages, and the licences the app must credit. MaxMind's
 * GeoLite attribution keeps the English original (EULA example); in Croatian a Croatian
 * sentence comes first, in German and French the translated sentence follows (BEAV-190).
 */
export function LegalLicencesScreen({ onBack, openLegal, openUrl }: LegalLicencesScreenProps) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language as Locale;
  const openMaxMind = () => openUrl(MAXMIND_URL);
  const sentence = (key: string, lang: Locale, testID: string) => (
    <Pressable
      key={testID}
      accessibilityRole="link"
      onPress={openMaxMind}
      style={styles.licenceRow}
      testID={`${testID}-row`}
    >
      <Text style={styles.licence} accessibilityLanguage={lang} testID={testID}>
        <Rich
          locale={lang}
          messageKey={key}
          values={{}}
          tag="link"
          onPress={openMaxMind}
          testID={`${testID}-link`}
        />
      </Text>
    </Pressable>
  );
  const english = sentence('legal.geolite', 'en', 'licence-geolite-en');
  const local = sentence('legal.geoliteLocal', locale, 'licence-geolite-local');
  const attribution = geoliteAttributionOrder(locale).map((part) =>
    part === 'english' ? english : local,
  );

  return (
    <Page title={t('settings.legal')} onBack={onBack} testID="legal-licences">
      <Heading style={styles.heading} inset={PAGE_HEADING_INSET} maxScale={TITLE_MAX_FONT_SCALE}>
        {t('settings.documents')}
      </Heading>
      <View style={styles.list}>
        {LEGAL_PAGES.map((page) => (
          <Pressable
            key={page}
            accessibilityRole="link"
            accessibilityLabel={t(`legal.documents.${LEGAL_PAGE_KINDS[page]}`)}
            onPress={() => openLegal(page, locale)}
            style={rowStyles.row}
            testID={`legal-licences-${page}`}
          >
            <Text style={rowStyles.label}>{t(`legal.documents.${LEGAL_PAGE_KINDS[page]}`)}</Text>
            <Text
              style={rowStyles.chevron}
              importantForAccessibility="no"
              accessibilityElementsHidden
            >
              ›
            </Text>
          </Pressable>
        ))}
      </View>
      <Heading style={styles.heading} inset={PAGE_HEADING_INSET} maxScale={TITLE_MAX_FONT_SCALE}>
        {t('settings.licences')}
      </Heading>
      <View style={styles.list} testID="licence-geolite">
        {attribution}
      </View>
    </Page>
  );
}

const styles = StyleSheet.create({
  heading: { ...textStyle('h2'), color: colors.ink },
  list: { gap: spacing.sm },
  licence: { ...pageStyles.small },
  // The whole sentence opens maxmind.com, so the tap target is at least 44pt, not just the
  // inline URL (BEAV-190 review R1-10, QA F8).
  licenceRow: { minHeight: minTapTarget, justifyContent: 'center' },
});

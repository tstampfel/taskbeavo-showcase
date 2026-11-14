import { LEGAL_PAGE_KINDS, LEGAL_PAGES, type LegalPage } from '@taskbeavo/app-links';
import { geoliteAttributionOrder, type Locale } from '@taskbeavo/i18n';
import { minTapTarget, spacing } from '@taskbeavo/ui-tokens';
import { useTranslation } from 'react-i18next';
import { Pressable, StyleSheet, View } from 'react-native';
import { Text } from '../components/ui/text';
import { ListCard, ListRow } from '../components/ui';
import { SectionLabel } from './settings-screen';
import { Page, styles as pageStyles } from './sign-in-screen';
import { richParts } from './terms-update-screen';

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
        {richParts({
          i18n,
          locale: lang,
          messageKey: key,
          values: {},
          tag: 'link',
          onPress: openMaxMind,
          testID: `${testID}-link`,
        })}
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
      <View style={styles.section}>
        <SectionLabel>{t('settings.documents')}</SectionLabel>
        <ListCard testID="legal-licences-documents">
          {LEGAL_PAGES.map((page) => (
            <ListRow
              key={page}
              role="link"
              title={t(`legal.documents.${LEGAL_PAGE_KINDS[page]}`)}
              onPress={() => openLegal(page, locale)}
              testID={`legal-licences-${page}`}
            />
          ))}
        </ListCard>
        <Text style={pageStyles.caption}>{t('settings.languagesNote')}</Text>
      </View>
      <View style={styles.section}>
        <SectionLabel>{t('settings.licences')}</SectionLabel>
        <View style={styles.list} testID="licence-geolite">
          {attribution}
        </View>
      </View>
    </Page>
  );
}

const styles = StyleSheet.create({
  section: { gap: spacing.sm },
  list: { gap: spacing.sm },
  licence: { ...pageStyles.small },
  // The whole sentence opens maxmind.com, so the tap target is at least 44pt, not just the
  // inline URL (BEAV-190 review R1-10, QA F8).
  licenceRow: { minHeight: minTapTarget, justifyContent: 'center' },
});

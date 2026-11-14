import { colors } from '@taskbeavo/ui-tokens';
import { useTranslation } from 'react-i18next';
import { StyleSheet } from 'react-native';
import { Heading, ListCard, ListRow } from '../components/ui';
import { textStyle } from '../components/ui/text-style';
import { Page, PAGE_HEADING_INSET } from './sign-in-screen';

/**
 * Settings: Business details (BEAV-199), Privacy and data (BEAV-83) and Legal and licences
 * (BEAV-190), until the account tab exists. One list card, as the mockups' settings pages
 * (19a, 21d).
 */
export function SettingsScreen({
  onBack,
  onBusiness,
  onPrivacy,
  onLegal,
}: {
  onBack(): void;
  onBusiness(): void;
  onPrivacy(): void;
  onLegal(): void;
}) {
  const { t } = useTranslation();
  return (
    <Page title={t('settings.title')} onBack={onBack} testID="settings">
      <ListCard testID="settings-list">
        <ListRow
          title={t('businessDetails.title')}
          onPress={onBusiness}
          testID="settings-business"
        />
        <ListRow title={t('privacySettings.title')} onPress={onPrivacy} testID="settings-privacy" />
        <ListRow title={t('settings.legal')} onPress={onLegal} testID="settings-legal" />
      </ListCard>
    </Page>
  );
}

/** A section label above a list card: the mockups' small bold capitals (`.c`, 21b, 19c). */
export function SectionLabel({ children }: { children: string }) {
  return (
    // Unlike page titles, a small label has no fixed cap: it grows as far as its words fit.
    <Heading style={styles.section} inset={PAGE_HEADING_INSET}>
      {children}
    </Heading>
  );
}

const styles = StyleSheet.create({
  section: { ...textStyle('caption'), color: colors.inkMuted, textTransform: 'uppercase' },
});

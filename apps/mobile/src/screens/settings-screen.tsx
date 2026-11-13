import { colors, minTapTarget, radii, spacing } from '@taskbeavo/ui-tokens';
import { useTranslation } from 'react-i18next';
import { Pressable, StyleSheet, Text } from 'react-native';
import { textStyle } from '../components/ui/text-style';
import { Page } from './sign-in-screen';

/**
 * Settings: Business details (BEAV-199), Privacy and data (BEAV-83) and Legal and licences
 * (BEAV-190), until the account tab exists.
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
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={t('businessDetails.title')}
        onPress={onBusiness}
        style={styles.row}
        testID="settings-business"
      >
        <Text style={styles.label}>{t('businessDetails.title')}</Text>
        <Text style={styles.chevron} importantForAccessibility="no" accessibilityElementsHidden>
          ›
        </Text>
      </Pressable>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={t('privacySettings.title')}
        onPress={onPrivacy}
        style={styles.row}
        testID="settings-privacy"
      >
        <Text style={styles.label}>{t('privacySettings.title')}</Text>
        <Text style={styles.chevron} importantForAccessibility="no" accessibilityElementsHidden>
          ›
        </Text>
      </Pressable>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={t('settings.legal')}
        onPress={onLegal}
        style={styles.row}
        testID="settings-legal"
      >
        <Text style={styles.label}>{t('settings.legal')}</Text>
        <Text style={styles.chevron} importantForAccessibility="no" accessibilityElementsHidden>
          ›
        </Text>
      </Pressable>
    </Page>
  );
}

export const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: spacing.md,
    minHeight: minTapTarget,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    borderRadius: radii.md,
    backgroundColor: colors.white,
  },
  label: { ...textStyle('body'), flex: 1, color: colors.ink },
  chevron: { ...textStyle('h2'), color: colors.green },
});

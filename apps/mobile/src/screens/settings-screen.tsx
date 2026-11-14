import { colors, typography } from '@taskbeavo/ui-tokens';
import { useTranslation } from 'react-i18next';
import { StyleSheet, useWindowDimensions } from 'react-native';
import { Heading, ListCard, ListRow } from '../components/ui';
import { CARD_HEADING_INSET, wordFitFontScale } from '../components/ui/heading';
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

/**
 * A section label: the mockups' small bold capitals (`.c`). Above a list card it is muted
 * (21d, 19c); `inCard` is the label inside a card, in ink (21b).
 */
export function SectionLabel({ children, inCard = false }: { children: string; inCard?: boolean }) {
  const { width } = useWindowDimensions();
  const inset = inCard ? CARD_HEADING_INSET : PAGE_HEADING_INSET;
  // Unlike page titles, a small label has no fixed cap: it grows as far as its words fit. They
  // are drawn in capitals, which are wider, so they are measured that way.
  const fit = wordFitFontScale(children.toUpperCase(), CAPTION_SIZE, width - inset);
  return (
    <Heading style={[styles.section, inCard && styles.inCard]} inset={inset} maxScale={fit}>
      {children}
    </Heading>
  );
}

const CAPTION_SIZE = typography.caption.fontSize;

const styles = StyleSheet.create({
  section: { ...textStyle('caption'), color: colors.inkMuted, textTransform: 'uppercase' },
  inCard: { color: colors.ink },
});

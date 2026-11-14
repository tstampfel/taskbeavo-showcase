import { colors, spacing } from '@taskbeavo/ui-tokens';
import { useTranslation } from 'react-i18next';
import { StyleSheet } from 'react-native';
import { Text } from './ui/text';
import { SafeAreaView } from 'react-native-safe-area-context';
import { fonts } from '../fonts';
import { Button, Heading } from './ui';

/** The translated "something went wrong" screen of the root error boundary. */
export function ErrorScreen({ onRetry }: { onRetry: () => void }) {
  const { t } = useTranslation();
  return (
    <SafeAreaView accessibilityRole="alert" style={styles.container}>
      <Heading style={styles.title}>{t('errorBoundary.title')}</Heading>
      <Text style={styles.body}>{t('errorBoundary.body')}</Text>
      <Button label={t('errorBoundary.retry')} onPress={onRetry} />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    justifyContent: 'center',
    gap: spacing.md,
    padding: spacing.lg,
    backgroundColor: colors.offWhite,
  },
  title: { fontFamily: fonts.bold, fontSize: 24, color: colors.green },
  body: { fontFamily: fonts.regular, fontSize: 16, color: colors.ink },
});

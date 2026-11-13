import { colors, spacing } from '@taskbeavo/ui-tokens';
import { useRouter } from 'expo-router';
import { useTranslation } from 'react-i18next';
import { ScrollView, StyleSheet, Text } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Button } from './ui';
import { textStyle } from './ui/text-style';

/** The screens links open; each has its title under `linkTarget.<screen>`. */
export type LinkTarget = 'task' | 'release' | 'noAccess' | 'conversation' | 'payouts';

/**
 * Placeholder for a screen a link opens, until the real screen is built. It only shows where
 * the link led; it never runs a query or mutation, so opening a link can't change anything.
 */
export function LinkTargetScreen({ target }: { target: LinkTarget }) {
  const { t } = useTranslation();
  const router = useRouter();

  return (
    <SafeAreaView style={styles.safeArea} testID={`link-target-${target}`}>
      <ScrollView contentContainerStyle={styles.content}>
        <Text accessibilityRole="header" style={[textStyle('h1'), styles.title]}>
          {t(`linkTarget.${target}`)}
        </Text>
        <Text style={[textStyle('body'), styles.body]}>{t('linkTarget.notReady')}</Text>
        <Text style={[textStyle('small'), styles.body]}>{t('linkTarget.safety')}</Text>
        <Button
          label={t('notFound.backHome')}
          variant="secondary"
          onPress={() => router.replace('/')}
        />
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safeArea: { flex: 1, backgroundColor: colors.offWhite },
  content: { flexGrow: 1, justifyContent: 'center', gap: spacing.lg, padding: spacing.xl },
  title: { color: colors.green },
  body: { color: colors.ink },
});

import { colors, spacing } from '@taskbeavo/ui-tokens';
import { useRouter } from 'expo-router';
import { useTranslation } from 'react-i18next';
import { StyleSheet } from 'react-native';
import { Text } from '../src/components/ui/text';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Button, Heading } from '../src/components/ui';
import { textStyle } from '../src/components/ui/text-style';

/** An in-app route that doesn't exist; incoming links are already filtered in +native-intent. */
export default function NotFound() {
  const { t } = useTranslation();
  const router = useRouter();
  return (
    <SafeAreaView style={styles.container} testID="not-found">
      <Heading style={[textStyle('h1'), styles.title]}>{t('notFound.title')}</Heading>
      <Text style={[textStyle('body'), styles.body]}>{t('notFound.body')}</Text>
      <Button label={t('notFound.backHome')} onPress={() => router.replace('/')} />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    justifyContent: 'center',
    gap: spacing.lg,
    padding: spacing.xl,
    backgroundColor: colors.offWhite,
  },
  title: { color: colors.green },
  body: { color: colors.ink },
});

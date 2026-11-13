import { useApolloClient, useQuery } from '@apollo/client/react';
import { ViewerDocument } from '@taskbeavo/graphql';
import { colors, spacing } from '@taskbeavo/ui-tokens';
import { useRouter } from 'expo-router';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { StyleSheet, Text, View } from 'react-native';
import { signOutEverywhere as signOutEverywhereHere, signOutHere } from '../auth/app-auth';
import { Button } from './ui';
import { textStyle } from './ui/text-style';

/**
 * "Log in", or who is signed in with "Sign out" and "Log out of all devices", until the
 * account tab exists.
 */
export function AccountRow({
  signOut = signOutHere,
  signOutEverywhere = signOutEverywhereHere,
}: {
  signOut?: () => Promise<void>;
  signOutEverywhere?: () => Promise<void>;
}) {
  const { t } = useTranslation();
  const router = useRouter();
  const client = useApolloClient();
  const { data } = useQuery(ViewerDocument, { fetchPolicy: 'cache-and-network' });
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const viewer = data?.viewer;

  function run(action: () => Promise<void>, onFail: () => void = () => undefined) {
    setBusy(true);
    setFailed(false);
    action()
      .then(() => client.refetchQueries({ include: [ViewerDocument] }))
      .catch(onFail)
      .finally(() => setBusy(false));
  }

  if (!viewer) {
    return (
      <Button label={t('signIn.signIn')} onPress={() => router.push('/sign-in')} testID="log-in" />
    );
  }
  return (
    <View style={styles.row} testID="signed-in">
      <Text style={styles.text}>{t('signIn.signedInAs', { name: viewer.firstName })}</Text>
      <Button
        label={t('signIn.signOut')}
        variant="secondary"
        busy={busy}
        onPress={() => run(signOut)}
        testID="sign-out"
      />
      <Button
        label={t('sessions.signOutEverywhere')}
        variant="secondary"
        busy={busy}
        onPress={() => run(signOutEverywhere, () => setFailed(true))}
        testID="sign-out-everywhere"
      />
      {failed ? (
        <Text style={styles.error} accessibilityRole="alert" testID="sign-out-everywhere-failed">
          {t('sessions.signOutEverywhereFailed')}
        </Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  row: { alignItems: 'center', gap: spacing.sm },
  text: { ...textStyle('small'), color: colors.ink, textAlign: 'center' },
  error: { ...textStyle('small'), color: colors.error, textAlign: 'center' },
});

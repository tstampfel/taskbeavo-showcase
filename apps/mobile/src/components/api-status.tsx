import { useQuery } from '@apollo/client/react';
import { ApiVersionDocument } from '@taskbeavo/graphql';
import { colors } from '@taskbeavo/ui-tokens';
import { useTranslation } from 'react-i18next';
import { StyleSheet, Text } from 'react-native';
import { fonts } from '../fonts';

/** Sample query: shows that the app reaches the API through Apollo. */
export function ApiStatus() {
  const { t } = useTranslation();
  const { data, error, loading } = useQuery(ApiVersionDocument);

  let text = t('apiStatus.connecting');
  if (data) text = t('apiStatus.connected', { version: data.apiVersion });
  else if (error && !loading) text = t('apiStatus.unavailable');

  return (
    <Text accessibilityLiveRegion="polite" testID="api-status" style={styles.status}>
      {text}
    </Text>
  );
}

const styles = StyleSheet.create({
  status: {
    fontFamily: fonts.regular,
    fontSize: 14,
    textAlign: 'center',
    color: colors.ink,
  },
});

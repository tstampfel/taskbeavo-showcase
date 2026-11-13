import { useMutation, useQuery } from '@apollo/client/react';
import { ConsentsDocument, SetConsentDocument, type ConsentPurpose } from '@taskbeavo/graphql';
import { defaultLocale, isLocale, toApiLocale, type Locale } from '@taskbeavo/i18n';
import { colors, minTapTarget, radii, spacing } from '@taskbeavo/ui-tokens';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ActivityIndicator, StyleSheet, Switch, Text, View } from 'react-native';
import { errorMessage } from '../components/sign-in/error-message';
import { textStyle } from '../components/ui/text-style';
import { ErrorText, Page, styles as pageStyles } from './sign-in-screen';

const SWITCHES: { purpose: ConsentPurpose; label: string; description: string }[] = [
  {
    purpose: 'ANALYTICS',
    label: 'privacySettings.analytics',
    description: 'privacySettings.analyticsApp',
  },
  {
    purpose: 'MARKETING_EMAIL',
    label: 'privacySettings.marketingEmail',
    description: 'privacySettings.marketingBody',
  },
  {
    purpose: 'MARKETING_PUSH',
    label: 'privacySettings.marketingPush',
    description: 'privacySettings.marketingBody',
  },
];

/**
 * Settings → Privacy and data (BEAV-83, mockup 19 "Privacy & data"): analytics, and news and
 * offers by email and by push, all off until the user agrees. Every change is a CONSENT row
 * on the server; a refusal rolls the switch back and says why. Booking and payment messages
 * can't be switched off, so they aren't here. The app has no analytics SDK yet, so the
 * analytics switch only records the choice; one added later must ask it first. The mockup's
 * "price-suggestion statistics" switch is left out: TaskBeavo never suggests prices (hard
 * rule k).
 */
export function PrivacyScreen({ onBack }: { onBack(): void }) {
  const { t, i18n } = useTranslation();
  const locale: Locale = isLocale(i18n.language) ? i18n.language : defaultLocale;
  const { data, error: loadError } = useQuery(ConsentsDocument, {
    fetchPolicy: 'cache-and-network',
  });
  const [save, { loading }] = useMutation(SetConsentDocument);
  const [error, setError] = useState<string | null>(null);

  const change = (purpose: ConsentPurpose, granted: boolean) => {
    setError(null);
    const current = data?.consents.find((consent) => consent.purpose === purpose);
    // The switch moves at once; a refusal rolls it back. __typename lets the cache file it.
    const optimistic = {
      __typename: 'ConsentState' as const,
      id: current?.id ?? purpose,
      purpose,
      granted,
      version: current?.version ?? null,
      givenAt: current?.givenAt ?? null,
    };
    save({
      variables: { purpose, granted, language: toApiLocale(locale) },
      optimisticResponse: { setConsent: optimistic },
    }).catch((caught: unknown) => setError(errorMessage(t, caught)));
  };

  return (
    <Page title={t('privacySettings.title')} onBack={onBack} testID="privacy">
      {!data && !loadError && <ActivityIndicator accessibilityLabel={t('privacySettings.title')} />}
      {data &&
        SWITCHES.map(({ purpose, label, description }) => {
          const granted = data.consents.find((c) => c.purpose === purpose)?.granted ?? false;
          return (
            <View key={purpose} style={styles.row}>
              <View style={styles.text}>
                <Text style={styles.label}>{t(label)}</Text>
                <Text style={pageStyles.muted}>{t(description)}</Text>
              </View>
              <Switch
                accessibilityLabel={t(label)}
                accessibilityHint={t(description)}
                value={granted}
                disabled={loading}
                onValueChange={(value) => change(purpose, value)}
                trackColor={{ true: colors.green, false: colors.control }}
                testID={`privacy-${purpose}`}
              />
            </View>
          );
        })}
      <ErrorText error={error ?? (loadError ? errorMessage(t, loadError) : null)} />
      <Text style={pageStyles.muted}>{t('privacySettings.footnote')}</Text>
    </Page>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    minHeight: minTapTarget,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    borderRadius: radii.md,
    backgroundColor: colors.white,
  },
  text: { flex: 1, gap: spacing.xs },
  label: { ...textStyle('body'), color: colors.ink },
});

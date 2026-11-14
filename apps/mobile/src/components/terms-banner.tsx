import { useQuery } from '@apollo/client/react';
import { TermsStatusDocument, ViewerDocument } from '@taskbeavo/graphql';
import { toApiLocale, type Locale } from '@taskbeavo/i18n';
import { colors, minTapTarget, radii, spacing } from '@taskbeavo/ui-tokens';
import { useRouter } from 'expo-router';
import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Pressable, StyleSheet, View } from 'react-native';
import { Text } from './ui/text';
import { fonts } from '../fonts';
import { textStyle } from './ui/text-style';

// The account and terms version "We've updated our terms" opened for in this launch: it
// opens by itself once per launch, account and version, so another account signing in, or a
// newer version taking effect meanwhile, gets it again.
let shownTo: string | null = null;

function shownKey(viewerId: string, version: string | undefined): string {
  return `${viewerId}:${version ?? ''}`;
}

/** Called by the screen itself too, so a launch that opens it first doesn't open it twice. */
export function markTermsUpdateShown(viewerId: string, version: string | undefined): void {
  shownTo = shownKey(viewerId, version);
}

/** For tests: start as a fresh launch. */
export function resetTermsBannerForTests(): void {
  shownTo = null;
}

/**
 * Limited mode after a terms change (mockup 21c). While `viewer.mustAcceptTerms`, the first
 * time this shows the account in a launch it opens "We've updated our terms" (unless the
 * launch opened it already); after "Not now" it says why
 * posting and booking are refused, and leads back. The API enforces the limit
 * (TERMS_UPDATE_REQUIRED); this only explains it.
 */
export function TermsBanner() {
  const { t, i18n } = useTranslation();
  const router = useRouter();
  // Signed out, nothing more is asked.
  const viewer = useQuery(ViewerDocument, { fetchPolicy: 'cache-first' }).data?.viewer;
  const { data } = useQuery(TermsStatusDocument, {
    variables: { language: toApiLocale(i18n.language as Locale) },
    fetchPolicy: 'cache-and-network',
    skip: !viewer,
  });
  const mustAccept = Boolean(viewer) && data?.viewer?.mustAcceptTerms === true;

  const viewerId = viewer?.id;
  const version = data?.viewer?.termsToAccept?.version;

  useEffect(() => {
    if (!mustAccept || !viewerId || shownTo === shownKey(viewerId, version)) return;
    markTermsUpdateShown(viewerId, version);
    router.push('/terms-update');
  }, [mustAccept, viewerId, version, router]);

  if (!mustAccept) return null;
  return (
    <View style={styles.banner} testID="terms-banner">
      <Text style={styles.title}>{t('termsUpdate.bannerTitle')}</Text>
      <Text style={styles.body}>{t('termsUpdate.bannerBody')}</Text>
      <Pressable
        accessibilityRole="link"
        onPress={() => router.push('/terms-update')}
        style={styles.action}
        testID="terms-banner-review"
      >
        <Text style={styles.link}>{t('termsUpdate.bannerAction')}</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  banner: {
    alignSelf: 'stretch',
    gap: spacing.xs,
    padding: spacing.lg,
    borderRadius: radii.md,
    backgroundColor: colors.orangeTint,
  },
  title: { ...textStyle('label'), color: colors.orangeTintText },
  body: { ...textStyle('small'), color: colors.orangeTintText },
  action: { minHeight: minTapTarget, justifyContent: 'center', alignSelf: 'flex-start' },
  link: {
    ...textStyle('small'),
    fontFamily: fonts.bold,
    color: colors.orangeTintText,
    textDecorationLine: 'underline',
  },
});

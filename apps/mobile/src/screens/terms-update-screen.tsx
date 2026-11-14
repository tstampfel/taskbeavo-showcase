import { useMutation, useQuery } from '@apollo/client/react';
import {
  AcceptCurrentTermsDocument,
  TermsStatusDocument,
  TermsToAcceptDocument,
  isTermsNotAccepted,
} from '@taskbeavo/graphql';
import { CLOSE_ACCOUNT_PAGE } from '@taskbeavo/app-links';
import { formatDate, toApiLocale, type Locale } from '@taskbeavo/i18n';
import { IntlMessageFormat } from 'intl-messageformat';
import { useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { colors, spacing, typography } from '@taskbeavo/ui-tokens';
import {
  AccessibilityInfo,
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  View,
  useWindowDimensions,
} from 'react-native';
import { Checkbox } from '../components/sign-in/checkbox';
import { errorMessage } from '../components/sign-in/error-message';
import { markTermsUpdateShown } from '../components/terms-banner';
import { Button, Card, Heading } from '../components/ui';
import { SectionLabel } from './settings-screen';
import { ErrorText, Page, PAGE_HEADING_INSET, styles } from './sign-in-screen';

export interface TermsUpdateScreenProps {
  /** Accepted, "Not now", or nothing to accept. */
  onDone(): void;
  /** Signed out meanwhile. */
  onSignedOut(): void;
  /** Opens a legal page in the in-app browser. */
  openLegal(page: 'terms' | 'helper-terms' | typeof CLOSE_ACCOUNT_PAGE, locale: Locale): void;
}

type Shown = { kind: string; version: string; date: string };

/**
 * "We've updated our terms" (mockup 21b). While `viewer.mustAcceptTerms`, the account is in
 * limited mode until it accepts `viewer.termsToAccept`, in the app's language, with an
 * unticked box that names its version and effective date: what the API records (BEAV-188).
 * The API checks the version (TERMS_NOT_ACCEPTED if it changed meanwhile) and records the
 * acceptance with this session's platform; "Not now" leaves the account limited. Checkout
 * never counts as accepting (user decision 2026-11-05). A HELPER_TERMS document is named and
 * opened as the Helper terms. Laid out as mockup 21b: no top bar, the "What's changed" card
 * with ticked lines, the full-terms link, and a small "Not now" under Accept; the refusal line
 * and the tick box are the app's own (legal requirements the mockup leaves out).
 */
export function TermsUpdateScreen({ onDone, onSignedOut, openLegal }: TermsUpdateScreenProps) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language as Locale;
  const status = useQuery(TermsStatusDocument, {
    variables: { language: toApiLocale(locale) },
    fetchPolicy: 'network-only',
  });
  const terms = useQuery(TermsToAcceptDocument, {
    variables: { language: toApiLocale(locale) },
    fetchPolicy: 'network-only',
  });
  const [accept, { loading: busy }] = useMutation(AcceptCurrentTermsDocument, {
    refetchQueries: [TermsStatusDocument],
  });
  const [agreed, setAgreed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const viewer = status.data?.viewer;
  const document = terms.data?.viewer?.termsToAccept;
  const loaded = !status.loading && status.data !== undefined;

  useEffect(() => {
    // Opened first (a link or a notification): "Not now" mustn't open it again.
    if (viewer?.mustAcceptTerms) markTermsUpdateShown(viewer.id, viewer.termsToAccept?.version);
  }, [viewer]);

  useEffect(() => {
    if (!loaded) return;
    if (!viewer) onSignedOut();
    // Nothing to accept (accepted on another device, or never needed): carry on.
    else if (!viewer.mustAcceptTerms) onDone();
  }, [loaded, viewer, onSignedOut, onDone]);

  const submit = async () => {
    if (!document || !agreed || busy) return;
    setError(null);
    try {
      await accept({ variables: { version: document.version, language: toApiLocale(locale) } });
      AccessibilityInfo.announceForAccessibility(t('termsUpdate.accepted'));
      onDone();
    } catch (caught) {
      setError(errorMessage(t, caught));
      if (isTermsNotAccepted(caught)) {
        // A newer version took effect while the screen was open: read and tick that one.
        setAgreed(false);
        void terms.refetch().catch(() => undefined);
      }
    }
  };

  if (!status.data && status.error && !status.loading) {
    // Offline or a server error: say so, with a way to try again or carry on limited.
    return (
      <Page
        testID="terms-update-load-error"
        footer={
          <>
            <Button
              label={t('termsUpdate.retry')}
              onPress={() => void status.refetch().catch(() => undefined)}
              testID="terms-update-status-retry"
            />
            <Button
              label={t('termsUpdate.notNow')}
              variant="ghost"
              size="sm"
              onPress={onDone}
              testID="terms-update-not-now"
            />
          </>
        }
      >
        <Heading style={styles.h1} inset={PAGE_HEADING_INSET}>
          {t('termsUpdate.title')}
        </Heading>
        <ErrorText error={t('termsUpdate.unavailable')} />
      </Page>
    );
  }

  if (!viewer?.mustAcceptTerms) {
    return (
      <Page testID="terms-update-loading">
        <ActivityIndicator accessibilityLabel={t('termsUpdate.title')} />
      </Page>
    );
  }

  const unavailable = !terms.loading && !document;
  const page = document?.kind === 'HELPER_TERMS' ? 'helper-terms' : 'terms';
  const shown: Shown | null = document
    ? {
        kind: document.kind,
        version: document.version,
        date: formatDate(String(document.effectiveAt), locale, 'long'),
      }
    : null;
  const kind = shown?.kind ?? 'TERMS';
  // The Privacy notice until account deletion exists (CLOSE_ACCOUNT_PAGE).
  const openClose = () => openLegal(CLOSE_ACCOUNT_PAGE, locale);

  return (
    <Page
      testID="terms-update"
      footer={
        <>
          <ErrorText error={error} />
          <Button
            label={t('termsUpdate.accept')}
            disabled={!document || !agreed}
            busy={busy}
            onPress={() => void submit()}
            testID="terms-update-accept"
          />
          {unavailable ? (
            <>
              <Text style={styles.mutedCenter} accessibilityLiveRegion="polite">
                {t('termsUpdate.unavailable')}
              </Text>
              <Button
                label={t('termsUpdate.retry')}
                variant="secondary"
                onPress={() => void terms.refetch().catch(() => undefined)}
                testID="terms-update-retry"
              />
            </>
          ) : !agreed ? (
            <Text style={styles.mutedCenter}>{t('termsUpdate.tickToAccept')}</Text>
          ) : null}
          <Button
            label={t('termsUpdate.notNow')}
            variant="ghost"
            size="sm"
            disabled={busy}
            onPress={onDone}
            testID="terms-update-not-now"
          />
        </>
      }
    >
      <Heading style={styles.h1} inset={PAGE_HEADING_INSET}>
        {t('termsUpdate.title')}
      </Heading>
      {shown ? (
        <Text style={styles.muted} testID="terms-update-version">
          {t('termsUpdate.version', shown)}
        </Text>
      ) : null}
      <Card testID="terms-update-changes">
        <SectionLabel>{t('termsUpdate.whatsChanged')}</SectionLabel>
        {/* NEEDS REVIEW (lawyer): the per-version summary of changes is legal text and comes
            from the lawyer with BEAV-182; until then a marked draft. The bookings and refusal
            lines are DRAFT legal sentences too (BEAV-39). */}
        <Ticked>{t('termsUpdate.summaryDraft', { kind })}</Ticked>
        {shown ? (
          <Ticked testID="terms-update-bookings">{t('termsUpdate.bookingsKeep', shown)}</Ticked>
        ) : null}
      </Card>
      <Pressable
        accessibilityRole="link"
        onPress={() => openLegal(page, locale)}
        style={styles.linkButton}
        testID="terms-update-read"
      >
        <Text style={styles.link}>{t('termsUpdate.readFull', { kind })}</Text>
      </Pressable>
      <Text style={styles.small} testID="terms-update-refuse">
        <Rich
          locale={locale}
          messageKey="termsUpdate.refuse"
          values={{}}
          tag="close"
          onPress={openClose}
          testID="terms-update-close"
        />
      </Text>
      {/* Only once the document is loaded: the line names its version and date (R1-1). */}
      {shown ? (
        <Checkbox
          checked={agreed}
          onChange={setAgreed}
          accessibilityLabel={t('termsUpdate.agree', shown).replace(/<\/?terms>/g, '')}
          label={
            <Text style={styles.small}>
              <Rich
                locale={locale}
                messageKey="termsUpdate.agree"
                values={shown}
                tag="terms"
                onPress={() => openLegal(page, locale)}
                testID="terms-update-agree-link"
              />
            </Text>
          }
          testID="terms-update-agree"
        />
      ) : null}
    </Page>
  );
}

/** A line of the "What's changed" card, after a green tick (21b). */
function Ticked({ children, testID }: { children: string; testID?: string }) {
  // As tall as the first line of the text at the user's text size, so the tick stays on it.
  const { fontScale } = useWindowDimensions();
  return (
    <View style={tickStyles.row}>
      <View
        style={[tickStyles.box, { height: typography.small.lineHeight * fontScale }]}
        importantForAccessibility="no-hide-descendants"
        accessibilityElementsHidden
        testID="terms-update-tick"
      >
        <View style={tickStyles.tick} />
      </View>
      <Text style={[styles.small, tickStyles.text]} testID={testID}>
        {children}
      </Text>
    </View>
  );
}

const TICK_WIDTH = 12;
const TICK_STROKE = 2;

const tickStyles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'flex-start', gap: spacing.sm },
  box: {
    width: spacing.lg,

    alignItems: 'center',
    justifyContent: 'center',
  },
  // A tick: the bottom and right sides of a box, turned 45°.
  tick: {
    width: TICK_WIDTH / 2,
    height: TICK_WIDTH,
    marginTop: -TICK_STROKE,
    borderRightWidth: TICK_STROKE,
    borderBottomWidth: TICK_STROKE,
    borderColor: colors.green,
    transform: [{ rotate: '45deg' }],
  },
  text: { flex: 1 },
});

/** A catalogue message with one tag rendered as an inline link. */
export function Rich({
  locale,
  messageKey,
  values,
  tag,
  onPress,
  testID,
}: {
  locale: Locale;
  messageKey: string;
  values: Record<string, string>;
  tag: string;
  onPress(): void;
  testID: string;
}) {
  const { i18n } = useTranslation();
  const message = String(i18n.getResource(locale, 'translation', messageKey) ?? '');
  const parts = new IntlMessageFormat(message, locale).format<ReactNode>({
    ...values,
    [tag]: (chunks: ReactNode[]) => (
      <Text
        key={tag}
        accessibilityRole="link"
        onPress={onPress}
        style={styles.inlineLink}
        testID={testID}
      >
        {chunks}
      </Text>
    ),
  });
  return <>{parts}</>;
}

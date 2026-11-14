import { useMutation, useQuery } from '@apollo/client/react';
import {
  ClearPosterBusinessDocument,
  PosterBusinessDocument,
  SetPosterBusinessDocument,
  signInErrorOf,
  type PosterBusinessKind,
} from '@taskbeavo/graphql';
import { borderWidth, colors, minTapTarget, radii, spacing } from '@taskbeavo/ui-tokens';
import type { TFunction } from 'i18next';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ActivityIndicator, Pressable, StyleSheet, View } from 'react-native';
import { Text } from '../components/ui/text';
import { errorMessage } from '../components/sign-in/error-message';
import { Button, Card, Input, Note, Sheet } from '../components/ui';
import { textStyle } from '../components/ui/text-style';
import { fonts } from '../fonts';
import { ErrorText, Page, styles as pageStyles } from './sign-in-screen';

const KINDS: PosterBusinessKind[] = ['OBRT', 'COMPANY'];

/** The OIB codes belong to the OIB field; the poster-specific wording replaces the helper's. */
function oibError(t: TFunction, error: unknown): string | null {
  const { code } = signInErrorOf(error);
  if (code === 'INVALID_OIB') return t('businessDetails.invalidOib');
  if (code === 'INVALID_BUSINESS_OIB') return t('businessDetails.invalidObrtOib');
  return null;
}

export interface BusinessDetailsScreenProps {
  onBack(): void;
  /** Signed out meanwhile. */
  onSignedOut(): void;
}

/**
 * Settings → Business details (BEAV-199): the obrt or company a poster books for, through the
 * BEAV-195 operations. Saving asks first, because a business poster books registered
 * businesses only and private helpers' open offers on their tasks are withdrawn. Only the
 * masked OIB ever comes back, so editing asks for the OIB again. The form follows mockup 14c's
 * business branch: the kind as radio cards, then the name and OIB fields.
 */
export function BusinessDetailsScreen({ onBack, onSignedOut }: BusinessDetailsScreenProps) {
  const { t } = useTranslation();
  const { data, loading, error, refetch } = useQuery(PosterBusinessDocument, {
    fetchPolicy: 'network-only',
    notifyOnNetworkStatusChange: true,
  });
  const viewer = data?.viewer;
  const signedOut = !loading && data !== undefined && !viewer;
  const [editing, setEditing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (signedOut) onSignedOut();
  }, [signedOut, onSignedOut]);

  if (!viewer && error && !loading) {
    return (
      <Page
        testID="business-load-error"
        title={t('businessDetails.title')}
        onBack={onBack}
        footer={
          <Button
            label={t('errorBoundary.retry')}
            onPress={() => void refetch().catch(() => undefined)}
            testID="business-retry"
          />
        }
      >
        <ErrorText error={errorMessage(t, error)} />
      </Page>
    );
  }

  if (!viewer) {
    return (
      <Page testID="business-loading" title={t('businessDetails.title')} onBack={onBack}>
        <ActivityIndicator accessibilityLabel={t('businessDetails.title')} />
      </Page>
    );
  }

  const business = viewer.posterBusiness ?? null;
  if (editing) {
    return (
      <BusinessForm
        business={business}
        onBack={onBack}
        onCancel={() => setEditing(false)}
        onSaved={() => {
          setEditing(false);
          setNotice(t('businessDetails.saved'));
        }}
      />
    );
  }
  return (
    <BusinessSummary
      business={business}
      notice={notice}
      onBack={onBack}
      onEdit={() => {
        setNotice(null);
        setEditing(true);
      }}
      onRemoved={() => setNotice(t('businessDetails.removed'))}
    />
  );
}

type Business = { kind: PosterBusinessKind; name: string; oibMasked: string } | null;

function BusinessSummary({
  business,
  notice,
  onBack,
  onEdit,
  onRemoved,
}: {
  business: Business;
  notice: string | null;
  onBack(): void;
  onEdit(): void;
  onRemoved(): void;
}) {
  const { t } = useTranslation();
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [clear, { loading }] = useMutation(ClearPosterBusinessDocument);
  const remove = () => {
    setConfirming(false);
    setError(null);
    clear().then(onRemoved, (caught: unknown) => setError(errorMessage(t, caught)));
  };

  return (
    <Page testID="business-details" title={t('businessDetails.title')} onBack={onBack}>
      {notice ? (
        <View accessibilityRole="alert" accessibilityLiveRegion="polite">
          <Note testID="business-notice">{notice}</Note>
        </View>
      ) : null}
      <Text style={pageStyles.muted}>{t('businessDetails.intro')}</Text>
      {business ? (
        <Card testID="business-saved">
          <Text style={styles.name}>{business.name}</Text>
          <Text style={pageStyles.small}>{t(`businessDetails.kind.${business.kind}`)}</Text>
          <Text style={pageStyles.small} testID="business-oib">
            {t('businessDetails.savedOib', { oib: business.oibMasked })}
          </Text>
          <View style={styles.actions}>
            <Button
              label={t('businessDetails.edit')}
              variant="secondary"
              size="sm"
              disabled={loading}
              onPress={onEdit}
              testID="business-edit"
            />
            <Button
              label={t('businessDetails.remove')}
              variant="ghost"
              size="sm"
              busy={loading}
              onPress={() => setConfirming(true)}
              testID="business-remove"
            />
          </View>
        </Card>
      ) : (
        <>
          <Text style={pageStyles.small} testID="business-none">
            {t('businessDetails.privatePoster')}
          </Text>
          <Button label={t('businessDetails.add')} onPress={onEdit} testID="business-add" />
        </>
      )}
      <ErrorText error={error} />
      <Sheet
        visible={confirming}
        onClose={() => setConfirming(false)}
        title={t('businessDetails.removeTitle')}
        closeLabel={t('businessDetails.cancel')}
        testID="business-remove-sheet"
      >
        <Text style={pageStyles.small}>{t('businessDetails.removeBody')}</Text>
        <Button
          label={t('businessDetails.confirmRemove')}
          onPress={remove}
          testID="business-remove-confirm"
        />
        <Button
          label={t('businessDetails.cancel')}
          variant="ghost"
          onPress={() => setConfirming(false)}
          testID="business-remove-cancel"
        />
      </Sheet>
    </Page>
  );
}

function BusinessForm({
  business,
  onBack,
  onCancel,
  onSaved,
}: {
  business: Business;
  onBack(): void;
  onCancel(): void;
  onSaved(): void;
}) {
  const { t } = useTranslation();
  const [kind, setKind] = useState<PosterBusinessKind>(business?.kind ?? 'OBRT');
  const [name, setName] = useState(business?.name ?? '');
  // Never prefilled: the API only returns the masked OIB.
  const [oib, setOib] = useState('');
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [save, { loading }] = useMutation(SetPosterBusinessDocument);

  const submit = () => {
    setConfirming(false);
    setError(null);
    setFieldError(null);
    save({ variables: { input: { kind, name: name.trim(), oib: oib.trim() } } }).then(
      () => {
        setOib('');
        onSaved();
      },
      (caught: unknown) => {
        const onField = oibError(t, caught);
        if (onField) setFieldError(onField);
        else setError(errorMessage(t, caught));
      },
    );
  };

  return (
    <Page
      testID="business-form"
      title={t('businessDetails.title')}
      onBack={onBack}
      footer={
        <>
          <Button
            label={t('businessDetails.save')}
            busy={loading}
            onPress={() => setConfirming(true)}
            testID="business-save"
          />
          <Button
            label={t('businessDetails.cancel')}
            variant="ghost"
            disabled={loading}
            onPress={onCancel}
            testID="business-cancel"
          />
        </>
      }
    >
      <Text style={pageStyles.label} importantForAccessibility="no">
        {t('businessDetails.kindLabel')}
      </Text>
      <View
        accessibilityRole="radiogroup"
        accessibilityLabel={t('businessDetails.kindLabel')}
        style={styles.kinds}
      >
        {KINDS.map((option) => {
          const selected = option === kind;
          return (
            <Pressable
              key={option}
              accessibilityRole="radio"
              accessibilityLabel={t(`businessDetails.kind.${option}`)}
              accessibilityState={{ selected, checked: selected }}
              onPress={() => {
                setKind(option);
                setFieldError(null);
              }}
              style={[styles.kind, selected && styles.kindOn]}
              testID={`business-kind-${option}`}
            >
              <View style={[styles.dot, selected && styles.dotOn]} />
              <Text style={styles.kindLabel} inset={KIND_LABEL_INSET}>
                {t(`businessDetails.kind.${option}`)}
              </Text>
            </Pressable>
          );
        })}
      </View>
      <Input
        label={t('businessDetails.nameLabel')}
        hint={t('businessDetails.nameHint')}
        autoComplete="organization"
        textContentType="organizationName"
        maxLength={200}
        value={name}
        onChangeText={setName}
        testID="business-name"
      />
      <Input
        label={t('businessDetails.oibLabel')}
        hint={t(kind === 'OBRT' ? 'businessDetails.oibHintObrt' : 'businessDetails.oibHintCompany')}
        error={fieldError ?? undefined}
        keyboardType="number-pad"
        autoComplete="off"
        maxLength={11}
        value={oib}
        onChangeText={(value) => {
          setOib(value);
          setFieldError(null);
        }}
        testID="business-oib-input"
      />
      {business ? <Text style={pageStyles.muted}>{t('businessDetails.oibReenter')}</Text> : null}
      <ErrorText error={error} />
      <Sheet
        visible={confirming}
        onClose={() => setConfirming(false)}
        title={t('businessDetails.confirmTitle')}
        closeLabel={t('businessDetails.cancel')}
        testID="business-confirm-sheet"
      >
        <Text style={pageStyles.small}>{t('businessDetails.confirmBody')}</Text>
        <Button
          label={t('businessDetails.confirmSave')}
          onPress={submit}
          testID="business-confirm"
        />
        <Button
          label={t('businessDetails.cancel')}
          variant="ghost"
          onPress={() => setConfirming(false)}
          testID="business-confirm-cancel"
        />
      </Sheet>
    </Page>
  );
}

const RADIO_DOT = 24;
/** The width beside a kind's label: the screen and card padding, borders, the radio and its gap. */
const KIND_LABEL_INSET =
  2 * spacing.lg + 2 * spacing.lg + 2 * borderWidth.focus + RADIO_DOT + spacing.md;

const styles = StyleSheet.create({
  actions: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  name: { ...textStyle('body'), fontFamily: fonts.bold, color: colors.ink },
  kinds: { gap: spacing.sm },
  // The mockups' selectable `.card` (14b, 14c): white, outlined green with a filled radio when
  // picked. The unpicked card keeps a hairline so its edge shows on off-white.
  kind: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    minHeight: minTapTarget + spacing.md,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    borderRadius: radii.lg,
    borderWidth: borderWidth.focus,
    borderColor: colors.line,
    backgroundColor: colors.white,
  },
  kindOn: { borderColor: colors.green },
  kindLabel: { ...textStyle('body'), fontFamily: fonts.bold, color: colors.ink, flex: 1 },
  // The mockups' `.radio`, as on the cancel screen.
  dot: {
    width: RADIO_DOT,
    height: RADIO_DOT,
    flexShrink: 0,
    borderRadius: radii.pill,
    borderWidth: 2,
    borderColor: colors.inkMuted,
    backgroundColor: colors.white,
  },
  dotOn: { borderWidth: 7, borderColor: colors.green },
});

import { useMutation, useQuery } from '@apollo/client/react';
import {
  JoinWaitlistDocument,
  MyCareContactsDocument,
  ViewerDocument,
  apiErrorCode,
} from '@taskbeavo/graphql';
import { defaultLocale, formatMoney, isLocale, toApiLocale, type Locale } from '@taskbeavo/i18n';
import { colors, spacing } from '@taskbeavo/ui-tokens';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { StyleSheet, View } from 'react-native';
import { Checkbox } from '../../components/sign-in/checkbox';
import { Button, Card, Heading, Input } from '../../components/ui';
import { Text } from '../../components/ui/text';
import { textStyle } from '../../components/ui/text-style';
import { i18n as appI18n } from '../../i18n';
import { listingWhen } from '../../tasks/listing-text';
import {
  parseBudgetCents,
  windowRange,
  type TaskDraft,
  type WizardStep,
} from '../../tasks/post-task-model';
import { ProblemText } from './simple-steps';

/** Mockup 03, 3g: what the poster is about to post, each part with Edit. */
export function ReviewStep({
  draft,
  onEdit,
}: {
  draft: TaskDraft;
  onEdit(step: WizardStep): void;
}) {
  const { t, i18n } = useTranslation();
  const locale: Locale = isLocale(i18n.language) ? i18n.language : defaultLocale;
  const contacts = useQuery(MyCareContactsDocument, { fetchPolicy: 'cache-first' });
  const contact = contacts.data?.myCareContacts.find((c) => c.id === draft.careContactId);
  const range =
    draft.dateMode === 'date' && draft.date && draft.timeWindow
      ? windowRange(draft.date, draft.timeWindow)
      : null;
  const when = listingWhen(
    {
      flexibleDate: range === null,
      windowStart: range?.start.toISOString() ?? null,
      windowEnd: range?.end.toISOString() ?? null,
      budgetCents: null,
      serviceAreaName: null,
      category: null,
    },
    t,
    locale,
  );
  const budgetCents = parseBudgetCents(draft.budget);

  const sections: { step: WizardStep; label: string; main: string; detail?: string }[] = [
    {
      step: 'what',
      label: t('postTask.review.what'),
      main: draft.title.trim(),
      detail: [
        draft.category ? t(`enums.TaskCategory.${draft.category}`) : null,
        draft.photos.length > 0
          ? t('postTask.review.photos', { count: draft.photos.length })
          : null,
      ]
        .filter(Boolean)
        .join(' · '),
    },
    {
      step: 'where',
      label: t('postTask.review.where'),
      main: draft.address,
      detail: contact
        ? t('postTask.review.onSite', { name: contact.name })
        : t('postTask.where.noContact'),
    },
    { step: 'when', label: t('postTask.review.when'), main: when },
    {
      step: 'budget',
      label: t('postTask.review.budget'),
      main: budgetCents === null ? '' : formatMoney(budgetCents, locale),
    },
  ];

  return (
    <>
      <Card testID="post-review">
        {sections.map((section) => (
          <View key={section.step} style={styles.section}>
            <View style={styles.grow}>
              <Text style={[textStyle('caption'), styles.muted]}>{section.label}</Text>
              <Text style={[textStyle('label'), styles.ink]} testID={`post-review-${section.step}`}>
                {section.main}
              </Text>
              {section.detail ? (
                <Text style={[textStyle('small'), styles.muted]}>{section.detail}</Text>
              ) : null}
            </View>
            <Button
              size="sm"
              variant="ghost"
              label={t('postTask.review.edit')}
              accessibilityLabel={t('postTask.review.editSection', { section: section.label })}
              onPress={() => onEdit(section.step)}
              testID={`post-edit-${section.step}`}
            />
          </View>
        ))}
      </Card>
      <Text style={[textStyle('small'), styles.muted]}>{t('postTask.review.privacy')}</Text>
    </>
  );
}

/**
 * Mockup 03, 3i: the address is outside every service area (the server said
 * OUT_OF_SERVICE_AREA, hard rule j). The poster can join the waitlist for the town or change
 * the address; the consent covers one launch email only.
 */
export function WaitlistCard({
  draft,
  onChangeAddress,
}: {
  draft: TaskDraft;
  onChangeAddress(): void;
}) {
  const { t, i18n } = useTranslation();
  const locale: Locale = isLocale(i18n.language) ? i18n.language : defaultLocale;
  const viewer = useQuery(ViewerDocument, { fetchPolicy: 'cache-first' }).data?.viewer;
  const [city, setCity] = useState(draft.city);
  const [email, setEmail] = useState<string | null>(null);
  const [consent, setConsent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [join, { data, loading }] = useMutation(JoinWaitlistDocument);
  const shownEmail = email ?? viewer?.email ?? '';
  const ready =
    city.trim() !== '' && shownEmail.trim() !== '' && consent && draft.category !== null;

  async function submit() {
    if (!draft.category) return;
    setError(null);
    try {
      await join({
        variables: {
          input: {
            email: shownEmail.trim(),
            city: city.trim(),
            countryCode: 'HR',
            category: draft.category,
            locale: toApiLocale(locale),
            consentLaunchEmail: consent,
          },
        },
      });
    } catch (failure) {
      const code = apiErrorCode(failure);
      setError(
        code && appI18n.exists(`errors.${code}`)
          ? t(`errors.${code}`)
          : t('postTask.waitlist.failed'),
      );
    }
  }

  return (
    <Card testID="post-waitlist">
      <Heading style={[textStyle('h2'), styles.ink]}>{t('postTask.waitlist.heading')}</Heading>
      <Text style={[textStyle('small'), styles.ink]}>{t('errors.OUT_OF_SERVICE_AREA')}</Text>
      {data?.joinWaitlist ? (
        <Text
          accessibilityLiveRegion="polite"
          style={[textStyle('label'), styles.green]}
          testID="post-waitlist-joined"
        >
          {t('postTask.waitlist.joined', { city: data.joinWaitlist.city })}
        </Text>
      ) : (
        <>
          <Input
            label={t('postTask.waitlist.city')}
            value={city}
            onChangeText={setCity}
            maxLength={100}
            testID="post-waitlist-city"
          />
          <Input
            label={t('postTask.waitlist.email')}
            value={shownEmail}
            onChangeText={setEmail}
            keyboardType="email-address"
            autoCapitalize="none"
            autoComplete="email"
            testID="post-waitlist-email"
          />
          <Checkbox
            checked={consent}
            onChange={setConsent}
            label={t('postTask.waitlist.consent')}
            testID="post-waitlist-consent"
          />
          {error ? <ProblemText text={error} /> : null}
          <Button
            label={t('postTask.waitlist.join')}
            busy={loading}
            disabled={!ready}
            onPress={() => void submit()}
            testID="post-waitlist-join"
          />
        </>
      )}
      <Button
        variant="secondary"
        label={t('postTask.waitlist.changeAddress')}
        onPress={onChangeAddress}
        testID="post-waitlist-change-address"
      />
    </Card>
  );
}

const styles = StyleSheet.create({
  section: { flexDirection: 'row', alignItems: 'flex-start', gap: spacing.sm },
  grow: { flex: 1, gap: spacing.xs },
  ink: { color: colors.ink },
  muted: { color: colors.inkMuted },
  green: { color: colors.green },
});

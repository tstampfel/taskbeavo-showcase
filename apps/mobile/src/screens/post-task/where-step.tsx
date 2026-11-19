import { useMutation, useQuery } from '@apollo/client/react';
import {
  CreateCareContactDocument,
  MyCareContactsDocument,
  apiErrorCode,
  type CareContactFieldsFragment,
} from '@taskbeavo/graphql';
import {
  defaultLocale,
  isLocale,
  languageNames,
  locales,
  toApiLocale,
  type Locale,
} from '@taskbeavo/i18n';
import { colors, spacing } from '@taskbeavo/ui-tokens';
import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AccessibilityInfo, StyleSheet, View } from 'react-native';
import { Checkbox } from '../../components/sign-in/checkbox';
import { Button, Card, Heading, Input, Note, Sheet } from '../../components/ui';
import { Text } from '../../components/ui/text';
import { textStyle } from '../../components/ui/text-style';
import { i18n as appI18n } from '../../i18n';
import type { AddressResult, AddressSearch } from '../../tasks/address-search';
import { MAX_ACCESS_DETAILS } from '../../tasks/post-task-model';
import { Choice, ChoiceGroup } from './choice';
import { ProblemText, problemFor, type StepProps } from './simple-steps';

type SearchState =
  | { phase: 'idle' }
  | { phase: 'searching' }
  | { phase: 'done'; results: AddressResult[] }
  | { phase: 'failed' };

/**
 * Mockup 03, 3c, 3d and 3j: the address, the on-site contact and the access details. The
 * address search runs only when the poster taps Search (Nominatim's usage policy), one request
 * at a time. The picked result's own coordinates go into the task; the server's service-area
 * check decides (hard rule j). None of it is logged or sent to Sentry (hard rule e).
 */
export function WhereStep({
  draft,
  change,
  problems,
  addressSearch,
}: StepProps & { addressSearch: AddressSearch }) {
  const { t, i18n } = useTranslation();
  const locale: Locale = isLocale(i18n.language) ? i18n.language : defaultLocale;
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState<SearchState>({ phase: 'idle' });
  const [adding, setAdding] = useState(false);
  const running = useRef(false);
  const contacts = useQuery(MyCareContactsDocument, { fetchPolicy: 'cache-and-network' });
  const problem = problemFor(problems, 'postTask.errors.address');
  const picked = draft.lat !== null && draft.lng !== null && draft.address !== '';

  async function runSearch() {
    if (running.current || query.trim().length === 0) return;
    running.current = true;
    setSearch({ phase: 'searching' });
    try {
      const results = await addressSearch.search(query, locale);
      setSearch({ phase: 'done', results });
      AccessibilityInfo.announceForAccessibility(
        results.length > 0
          ? t('postTask.where.resultsCount', { count: results.length })
          : t('postTask.where.noResults'),
      );
    } catch {
      setSearch({ phase: 'failed' });
    } finally {
      running.current = false;
    }
  }

  function pick(result: AddressResult) {
    change({ address: result.address, lat: result.lat, lng: result.lng, city: result.city });
    setSearch({ phase: 'idle' });
    setQuery('');
  }

  return (
    <>
      <Heading style={[textStyle('h1'), styles.ink]}>{t('postTask.where.heading')}</Heading>
      {picked ? (
        <Card variant="flat" testID="post-address-picked">
          <Text style={[textStyle('caption'), styles.muted]}>{t('postTask.where.picked')}</Text>
          <Text style={[textStyle('label'), styles.ink]}>{draft.address}</Text>
          <Button
            size="sm"
            variant="secondary"
            label={t('postTask.where.change')}
            onPress={() => change({ address: '', lat: null, lng: null, city: '' })}
            testID="post-address-change"
          />
        </Card>
      ) : (
        <View style={styles.group}>
          <Input
            label={t('postTask.where.addressLabel')}
            hint={t('postTask.where.addressHint')}
            value={query}
            onChangeText={setQuery}
            onSubmitEditing={() => void runSearch()}
            returnKeyType="search"
            autoComplete="street-address"
            error={problem ? t(problem) : undefined}
            testID="post-address"
          />
          <Button
            variant="secondary"
            label={t('postTask.where.search')}
            busy={search.phase === 'searching'}
            disabled={query.trim().length === 0}
            onPress={() => void runSearch()}
            testID="post-address-search"
          />
          {search.phase === 'failed' ? (
            <View style={styles.group} testID="post-address-failed">
              <ProblemText text={t('postTask.where.searchFailed')} />
              <Button
                size="sm"
                variant="ghost"
                label={t('postTask.where.retry')}
                onPress={() => void runSearch()}
              />
            </View>
          ) : null}
          {search.phase === 'done' ? (
            <View style={styles.group} testID="post-address-results">
              {search.results.length === 0 ? (
                <Text style={[textStyle('small'), styles.ink]}>
                  {t('postTask.where.noResults')}
                </Text>
              ) : (
                <ChoiceGroup label={t('postTask.where.resultsLabel')}>
                  {search.results.map((result, index) => (
                    <Choice
                      key={result.id}
                      shape="row"
                      label={result.address}
                      selected={false}
                      onPress={() => pick(result)}
                      testID={`post-address-result-${index}`}
                    />
                  ))}
                </ChoiceGroup>
              )}
              <Text style={[textStyle('caption'), styles.muted]} testID="post-address-attribution">
                {addressSearch.attribution}
              </Text>
            </View>
          ) : null}
        </View>
      )}
      <Note>{t('postTask.where.approximate')}</Note>

      <Text style={[textStyle('h2'), styles.ink]}>{t('postTask.where.contactHeading')}</Text>
      <ChoiceGroup label={t('postTask.where.contactHeading')}>
        <Choice
          shape="row"
          label={t('postTask.where.noContact')}
          selected={draft.careContactId === null}
          onPress={() => change({ careContactId: null })}
          testID="post-contact-none"
        />
        {(contacts.data?.myCareContacts ?? []).map((contact) => (
          <Choice
            key={contact.id}
            shape="row"
            label={contact.name}
            detail={contact.area}
            selected={draft.careContactId === contact.id}
            onPress={() => change({ careContactId: contact.id })}
            testID={`post-contact-${contact.id}`}
          />
        ))}
      </ChoiceGroup>
      {contacts.error && !contacts.data ? (
        <ProblemText text={t('postTask.where.contactsError')} />
      ) : null}
      <Button
        variant="secondary"
        label={t('postTask.where.addContact')}
        onPress={() => setAdding(true)}
        testID="post-contact-add"
      />
      {adding ? (
        <ContactSheet
          onClose={() => setAdding(false)}
          onSaved={(contact) => {
            change({ careContactId: contact.id });
            setAdding(false);
          }}
        />
      ) : null}

      <Input
        label={t('postTask.where.accessLabel')}
        hint={t('postTask.where.accessHint')}
        value={draft.accessDetails}
        onChangeText={(accessDetails) => change({ accessDetails })}
        maxLength={MAX_ACCESS_DETAILS}
        multiline
        textAlignVertical="top"
        testID="post-access"
      />
    </>
  );
}

/**
 * Mockup 03, 3d. The contact's phone stays in this sheet's state until it's saved with
 * createCareContact; the draft keeps only the contact's id, and closing the sheet drops it.
 */
function ContactSheet({
  onClose,
  onSaved,
}: {
  onClose(): void;
  onSaved(contact: CareContactFieldsFragment): void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState('');
  const [phone, setPhone] = useState('');
  const [area, setArea] = useState('');
  const [language, setLanguage] = useState<Locale>('hr');
  const [noSmartphone, setNoSmartphone] = useState(false);
  const [agreed, setAgreed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [create, { loading }] = useMutation(CreateCareContactDocument, {
    update(cache, { data }) {
      const created = data?.createCareContact;
      if (!created) return;
      cache.updateQuery({ query: MyCareContactsDocument }, (existing) =>
        existing ? { myCareContacts: [...existing.myCareContacts, created] } : existing,
      );
    },
  });
  const ready = name.trim() !== '' && phone.trim() !== '' && area.trim() !== '' && agreed;

  async function save() {
    setError(null);
    try {
      const { data } = await create({
        variables: {
          input: {
            name: name.trim(),
            phone: phone.trim(),
            area: area.trim(),
            language: toApiLocale(language),
            noSmartphone,
            hasTheirAgreement: agreed,
          },
        },
      });
      if (data?.createCareContact) onSaved(data.createCareContact);
    } catch (failure) {
      const code = apiErrorCode(failure);
      setError(
        code && appI18n.exists(`errors.${code}`)
          ? t(`errors.${code}`)
          : t('postTask.contact.failed'),
      );
    }
  }

  return (
    <Sheet
      visible
      onClose={onClose}
      title={t('postTask.contact.title')}
      closeLabel={t('postTask.contact.close')}
      testID="post-contact-sheet"
    >
      <Text style={[textStyle('small'), styles.muted]}>{t('postTask.contact.intro')}</Text>
      <Input
        label={t('postTask.contact.name')}
        value={name}
        onChangeText={setName}
        maxLength={100}
        testID="post-contact-name"
      />
      <Input
        label={t('postTask.contact.phone')}
        hint={t('postTask.contact.phoneHint')}
        value={phone}
        onChangeText={setPhone}
        keyboardType="phone-pad"
        autoComplete="off"
        testID="post-contact-phone"
      />
      <Input
        label={t('postTask.contact.area')}
        hint={t('postTask.contact.areaHint')}
        value={area}
        onChangeText={setArea}
        maxLength={100}
        testID="post-contact-area"
      />
      <ChoiceGroup label={t('postTask.contact.language')}>
        {locales.map((option) => (
          <Choice
            key={option}
            label={option.toUpperCase()}
            accessibilityLabel={languageNames[option]}
            selected={language === option}
            onPress={() => setLanguage(option)}
            testID={`post-contact-language-${option}`}
          />
        ))}
      </ChoiceGroup>
      <Checkbox
        checked={noSmartphone}
        onChange={setNoSmartphone}
        label={t('postTask.contact.noSmartphone')}
        testID="post-contact-no-smartphone"
      />
      <Checkbox
        checked={agreed}
        onChange={setAgreed}
        label={t('postTask.contact.agreement')}
        testID="post-contact-agreement"
      />
      {error ? <ProblemText text={error} testID="post-contact-error" /> : null}
      <Button
        label={t('postTask.contact.save')}
        busy={loading}
        disabled={!ready}
        onPress={() => void save()}
        testID="post-contact-save"
      />
    </Sheet>
  );
}

const styles = StyleSheet.create({
  group: { gap: spacing.md },
  ink: { color: colors.ink },
  muted: { color: colors.inkMuted },
});

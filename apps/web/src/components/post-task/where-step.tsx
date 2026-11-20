'use client';

import { useMutation, useQuery } from '@apollo/client/react';
import {
  CreateCareContactDocument,
  MAX_ACCESS_DETAILS,
  MyCareContactsDocument,
  apiErrorCode,
  type CareContactFieldsFragment,
} from '@taskbeavo/graphql';
import { languageNames, locales, toApiLocale, type Locale } from '@taskbeavo/i18n';
import { useTranslations } from 'next-intl';
import { useRef, useState } from 'react';
import { Button, Card, InfoIcon, Input, Note, Sheet } from '../ui';
import type { AddressResult, AddressSearch } from './address-search';
import styles from './post-task.module.css';
import {
  Choice,
  ChoiceGroup,
  ProblemText,
  StepHeading,
  TextArea,
  problemFor,
  type StepProps,
} from './steps';

type SearchState =
  | { phase: 'idle' }
  | { phase: 'searching' }
  | { phase: 'done'; results: AddressResult[] }
  | { phase: 'failed' };

/**
 * Mockup 03, 3c, 3d and 3j: the address, the on-site contact and the access details. The
 * address search runs only when the poster submits it (Nominatim's usage policy), one request
 * at a time; the picked result's own coordinates go into the task, and the server's
 * service-area check decides (hard rule j). None of it is logged or sent to Sentry (rule e).
 */
export function WhereStep({
  draft,
  change,
  problems,
  headingRef,
  addressSearch,
  locale,
}: StepProps & { addressSearch: AddressSearch; locale: Locale }) {
  const t = useTranslations();
  const tErrors = useTranslations('errors');
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
      setSearch({ phase: 'done', results: await addressSearch.search(query, locale) });
    } catch {
      // The draft is saved; the poster can try again.
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
      <StepHeading headingRef={headingRef}>{t('postTask.where.heading')}</StepHeading>
      {picked ? (
        <Card variant="flat" className={styles.group} data-testid="post-address-picked">
          <p className={`${styles.caption} ${styles.muted}`}>{t('postTask.where.picked')}</p>
          <p className={styles.label} data-testid="post-address-value">
            {draft.address}
          </p>
          <div>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => change({ address: '', lat: null, lng: null, city: '' })}
              data-testid="post-address-change"
            >
              {t('postTask.where.change')}
            </Button>
          </div>
        </Card>
      ) : (
        <div className={styles.group}>
          <form
            role="search"
            className={styles.searchRow}
            onSubmit={(event) => {
              event.preventDefault();
              void runSearch();
            }}
          >
            <Input
              label={t('postTask.where.addressLabel')}
              hint={t('postTask.where.addressHintWeb')}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              autoComplete="street-address"
              enterKeyHint="search"
              error={problem ? t(problem) : undefined}
              data-testid="post-address"
            />
            <Button
              type="submit"
              variant="secondary"
              disabled={query.trim().length === 0 || search.phase === 'searching'}
              aria-busy={search.phase === 'searching'}
              data-testid="post-address-search"
            >
              {t('postTask.where.search')}
            </Button>
          </form>
          <div role="status" aria-live="polite" className={styles.group}>
            {search.phase === 'done' ? (
              <p className={`${styles.small}`}>
                {search.results.length > 0
                  ? t('postTask.where.resultsCount', { count: search.results.length })
                  : t('postTask.where.noResults')}
              </p>
            ) : null}
          </div>
          {search.phase === 'failed' ? (
            <div className={styles.group} data-testid="post-address-failed">
              <ProblemText text={t('postTask.where.searchFailed')} />
              <div>
                <Button variant="ghost" size="sm" onClick={() => void runSearch()}>
                  {t('postTask.where.retry')}
                </Button>
              </div>
            </div>
          ) : null}
          {search.phase === 'done' ? (
            <div className={styles.group} data-testid="post-address-results">
              {search.results.length > 0 ? (
                <ul className={styles.results} aria-label={t('postTask.where.resultsLabel')}>
                  {search.results.map((result, index) => (
                    <li key={result.id}>
                      <button
                        type="button"
                        className={`${styles.choice} ${styles.row}`}
                        onClick={() => pick(result)}
                        data-testid={`post-address-result-${index}`}
                      >
                        {result.address}
                      </button>
                    </li>
                  ))}
                </ul>
              ) : null}
              <p
                className={`${styles.caption} ${styles.muted}`}
                data-testid="post-address-attribution"
              >
                {addressSearch.attribution}
              </p>
            </div>
          ) : null}
        </div>
      )}
      <Note icon={<InfoIcon />}>{t('postTask.where.approximate')}</Note>

      <div className={styles.group}>
        <ChoiceGroup legend={t('postTask.where.contactHeading')} rows>
          <Choice
            name="contact"
            row
            label={t('postTask.where.noContact')}
            checked={draft.careContactId === null}
            onSelect={() => change({ careContactId: null })}
            testId="post-contact-none"
          />
          {(contacts.data?.myCareContacts ?? []).map((contact) => (
            <Choice
              key={contact.id}
              name="contact"
              row
              label={contact.name}
              detail={contact.area}
              checked={draft.careContactId === contact.id}
              onSelect={() => change({ careContactId: contact.id })}
              testId={`post-contact-${contact.id}`}
            />
          ))}
        </ChoiceGroup>
        {contacts.error && !contacts.data ? (
          <ProblemText text={t('postTask.where.contactsError')} />
        ) : null}
        <div>
          <Button
            variant="secondary"
            onClick={() => setAdding(true)}
            data-testid="post-contact-add"
          >
            {t('postTask.where.addContact')}
          </Button>
        </div>
      </div>
      {adding ? (
        <ContactSheet
          onClose={() => setAdding(false)}
          onSaved={(contact) => {
            change({ careContactId: contact.id });
            setAdding(false);
          }}
          errorText={(code) => (code && tErrors.has(code) ? tErrors(code) : null)}
        />
      ) : null}

      <TextArea
        label={t('postTask.where.accessLabel')}
        hint={t('postTask.where.accessHint')}
        value={draft.accessDetails}
        onChange={(event) => change({ accessDetails: event.target.value })}
        maxLength={MAX_ACCESS_DETAILS}
        rows={3}
        autoComplete="off"
        data-testid="post-access"
      />
      <p className={`${styles.small} ${styles.muted}`} data-testid="post-access-not-saved">
        {t('postTask.where.accessNotSaved')}
      </p>
    </>
  );
}

/**
 * Mockup 03, 3d. The contact's phone stays in this sheet's state until createCareContact saves
 * it; the draft keeps only the contact's id, and closing the sheet drops it (hard rule e).
 */
function ContactSheet({
  onClose,
  onSaved,
  errorText,
}: {
  onClose(): void;
  onSaved(contact: CareContactFieldsFragment): void;
  errorText(code: string | null | undefined): string | null;
}) {
  const t = useTranslations();
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
    if (!ready || loading) return;
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
      setError(errorText(apiErrorCode(failure)) ?? t('postTask.contact.failed'));
    }
  }

  return (
    <Sheet
      open
      onClose={onClose}
      title={t('postTask.contact.title')}
      closeLabel={t('postTask.contact.close')}
    >
      <form
        className={styles.group}
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
        data-testid="post-contact-sheet"
      >
        <p className={`${styles.small} ${styles.muted}`}>{t('postTask.contact.intro')}</p>
        <Input
          label={t('postTask.contact.name')}
          value={name}
          onChange={(event) => setName(event.target.value)}
          maxLength={100}
          autoComplete="off"
          data-testid="post-contact-name"
        />
        <Input
          label={t('postTask.contact.phone')}
          hint={t('postTask.contact.phoneHint')}
          value={phone}
          onChange={(event) => setPhone(event.target.value)}
          type="tel"
          autoComplete="off"
          data-testid="post-contact-phone"
        />
        <Input
          label={t('postTask.contact.area')}
          hint={t('postTask.contact.areaHint')}
          value={area}
          onChange={(event) => setArea(event.target.value)}
          maxLength={100}
          autoComplete="off"
          data-testid="post-contact-area"
        />
        <ChoiceGroup legend={t('postTask.contact.language')}>
          {locales.map((option) => (
            <Choice
              key={option}
              name="contact-language"
              label={languageNames[option]}
              checked={language === option}
              onSelect={() => setLanguage(option)}
              testId={`post-contact-language-${option}`}
            />
          ))}
        </ChoiceGroup>
        <label className={styles.checkbox}>
          <input
            type="checkbox"
            checked={noSmartphone}
            onChange={(event) => setNoSmartphone(event.target.checked)}
            data-testid="post-contact-no-smartphone"
          />
          <span>{t('postTask.contact.noSmartphone')}</span>
        </label>
        <label className={styles.checkbox}>
          <input
            type="checkbox"
            checked={agreed}
            onChange={(event) => setAgreed(event.target.checked)}
            data-testid="post-contact-agreement"
          />
          <span>{t('postTask.contact.agreement')}</span>
        </label>
        {error ? <ProblemText text={error} testId="post-contact-error" /> : null}
        <Button
          type="submit"
          disabled={!ready || loading}
          aria-busy={loading}
          data-testid="post-contact-save"
        >
          {t('postTask.contact.save')}
        </Button>
      </form>
    </Sheet>
  );
}

'use client';

import { legalPath } from '@taskbeavo/app-links';
import { useQuery } from '@apollo/client/react';
import {
  CurrentTermsDocument,
  canCreateAccount,
  isTermsNotAccepted,
  type AccountDetailsForm as Form,
  type SignInProvider,
} from '@taskbeavo/graphql';
import { countryFlag, locales, sortedCountries, toApiLocale, type Locale } from '@taskbeavo/i18n';
import { useLocale, useTranslations } from 'next-intl';
import { useMemo, useState } from 'react';
import { Button, Input } from '../ui';
import { errorMessage } from './error-message';
import styles from './sign-in.module.css';

export interface AccountDetailsFormProps {
  email: string;
  provider: SignInProvider | undefined;
  initial: Pick<Form, 'firstName' | 'lastName' | 'country'>;
  /** Creates the account; throws the API's error. */
  onSubmit(form: Form & { termsVersion: string }): Promise<void>;
}

/**
 * The one account-details step every new account goes through, from an email code, Google or
 * Apple (mockups 15e and 15e2). Both boxes start unticked, and "Create account" stays disabled
 * until the 18+ and the terms boxes are ticked. The terms box accepts the version in force
 * (CurrentTerms) in the account's language; the API refuses any other (TERMS_NOT_ACCEPTED), and
 * records the acceptance with the account. Changing the language unticks the box.
 */
export function AccountDetailsForm({
  email,
  provider,
  initial,
  onSubmit,
}: AccountDetailsFormProps) {
  const t = useTranslations();
  const locale = useLocale() as Locale;
  const countries = useMemo(() => sortedCountries(locale), [locale]);
  const [form, setForm] = useState<Omit<Form, 'termsVersion'>>({
    ...initial,
    locale,
    isAdult: false,
    acceptedTerms: false,
  });
  // Every language has the same version; the form's language picks the text it links to.
  const terms = useQuery(CurrentTermsDocument, {
    variables: { language: toApiLocale(form.locale) },
    fetchPolicy: 'network-only',
  });
  const termsVersion = terms.data?.currentLegalDocument?.version;
  const termsUnavailable = !terms.loading && termsVersion === undefined;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ready = canCreateAccount({ ...form, termsVersion });

  function update(change: Partial<Omit<Form, 'termsVersion'>>) {
    setForm((current) => ({ ...current, ...change }));
  }

  async function submit() {
    if (!ready || busy || termsVersion === undefined) return;
    setBusy(true);
    setError(null);
    try {
      await onSubmit({ ...form, termsVersion });
    } catch (caught) {
      setError(errorMessage(t, caught));
      setBusy(false);
      if (isTermsNotAccepted(caught)) {
        // The terms changed while the form was open: accept the new version, not the old.
        update({ acceptedTerms: false });
        void terms.refetch();
      }
    }
  }

  return (
    <form
      className={styles.panel}
      data-testid="account-details"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <h1 className={styles.title}>
        {provider ? t('signIn.providerDetailsTitle') : t('signIn.detailsTitle')}
      </h1>
      {provider ? (
        <div className={styles.provider}>
          <strong className={styles.text}>{t('signIn.signedInWith', { provider })}</strong>
          <span className={styles.muted}>{email}</span>
        </div>
      ) : null}
      <Input
        label={t('signIn.firstName')}
        autoComplete="given-name"
        required
        value={form.firstName}
        onChange={(event) => update({ firstName: event.target.value })}
        data-testid="first-name"
      />
      <Input
        label={t('signIn.lastName')}
        autoComplete="family-name"
        required
        value={form.lastName}
        onChange={(event) => update({ lastName: event.target.value })}
        data-testid="last-name"
      />
      <label className={styles.fieldset}>
        <span className={styles.label}>{t('signIn.country')}</span>
        <select
          className={styles.select}
          autoComplete="country"
          value={form.country}
          onChange={(event) => update({ country: event.target.value })}
          data-testid="country"
        >
          {countries.map(({ code, name }) => (
            <option key={code} value={code}>
              {`${countryFlag(code)} ${name}`}
            </option>
          ))}
        </select>
      </label>
      <fieldset className={styles.fieldset}>
        <legend className={styles.label}>{t('signIn.appLanguage')}</legend>
        <div className={styles.segments}>
          {locales.map((option) => (
            <label key={option} className={styles.segment} lang={option}>
              <input
                type="radio"
                name="locale"
                value={option}
                checked={form.locale === option}
                // The box accepted the terms in the old language: tick it again for this one.
                onChange={() => update({ locale: option, acceptedTerms: false })}
              />
              <span>{option.toUpperCase()}</span>
            </label>
          ))}
        </div>
      </fieldset>
      <label className={styles.checkRow}>
        <input
          type="checkbox"
          checked={form.isAdult}
          onChange={(event) => update({ isAdult: event.target.checked })}
          data-testid="is-adult"
        />
        <span>{t('signIn.isAdult')}</span>
      </label>
      <label className={styles.checkRow}>
        <input
          type="checkbox"
          required
          checked={form.acceptedTerms}
          onChange={(event) => update({ acceptedTerms: event.target.checked })}
          data-testid="accept-terms"
        />
        <span>
          {/* In the account's language, which the API records the acceptance in. */}
          {t.rich('signIn.terms', {
            terms: (chunks) => (
              <a href={legalPath('terms', form.locale)} target="_blank" rel="noopener">
                {chunks}
              </a>
            ),
            privacy: (chunks) => (
              <a href={legalPath('privacy', form.locale)} target="_blank" rel="noopener">
                {chunks}
              </a>
            ),
          })}{' '}
          <abbr className={styles.required} title={t('signIn.required')}>
            *
          </abbr>
        </span>
      </label>
      {error ? (
        <p className={styles.error} role="alert">
          {error}
        </p>
      ) : null}
      <div className={styles.actions}>
        <Button type="submit" fullWidth disabled={!ready || busy} data-testid="create-account">
          {t('signIn.createAccount')}
        </Button>
        {termsUnavailable ? (
          <>
            <p className={styles.muted} style={{ textAlign: 'center' }} role="status">
              {t('signIn.termsUnavailable')}
            </p>
            <Button
              type="button"
              variant="secondary"
              fullWidth
              onClick={() => void terms.refetch().catch(() => undefined)}
              data-testid="terms-retry"
            >
              {t('signIn.termsRetry')}
            </Button>
          </>
        ) : !form.acceptedTerms ? (
          <p className={styles.muted} style={{ textAlign: 'center' }}>
            {t('signIn.termsRequired')}
          </p>
        ) : null}
      </div>
    </form>
  );
}

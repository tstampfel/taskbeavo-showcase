'use client';

import { CLOSE_ACCOUNT_PAGE, legalPath } from '@taskbeavo/app-links';
import { useMutation, useQuery } from '@apollo/client/react';
import {
  AcceptCurrentTermsDocument,
  TermsStatusDocument,
  TermsToAcceptDocument,
  isTermsNotAccepted,
} from '@taskbeavo/graphql';
import { formatDate, toApiLocale, type Locale } from '@taskbeavo/i18n';
import { useLocale, useTranslations } from 'next-intl';
import { useEffect, useState } from 'react';
import { safeNextPath } from '../../auth/auth-client';
import { useRouter } from '../../i18n/navigation';
import { errorMessage } from '../sign-in/error-message';
import styles from '../sign-in/sign-in.module.css';
import { Button } from '../ui';
import { markTermsUpdateShown } from './shown';
import own from './terms-update.module.css';

/**
 * "We've updated our terms" (mockup 21b). Shown while `viewer.mustAcceptTerms`: the account is
 * in limited mode until it accepts `viewer.termsToAccept`, in the page's language, with an
 * unticked box that names its version and effective date: what the API records (BEAV-188).
 * The API checks the version (TERMS_NOT_ACCEPTED if it changed meanwhile); "Not now" leaves
 * the account limited. Checkout never counts as accepting (user decision 2026-11-05). A
 * HELPER_TERMS document is named and linked as the Helper terms.
 */
export function TermsUpdate({ next }: { next: string | null }) {
  const t = useTranslations();
  const locale = useLocale() as Locale;
  const router = useRouter();
  const target = safeNextPath(next);
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

  useEffect(() => {
    // Reached directly (a link, a reload, back from sign-in): "Not now" mustn't land here again.
    if (viewer?.mustAcceptTerms) markTermsUpdateShown(viewer.id, viewer.termsToAccept?.version);
  }, [viewer]);

  useEffect(() => {
    if (status.loading || !status.data) return;
    if (!viewer) router.replace({ pathname: '/sign-in', query: { next: '/terms-update' } });
    // Nothing to accept (accepted in another tab, or never needed): carry on.
    else if (!viewer.mustAcceptTerms) router.replace(target);
  }, [status.loading, status.data, viewer, router, target]);

  async function submit() {
    if (!document || !agreed || busy) return;
    setError(null);
    try {
      await accept({ variables: { version: document.version, language: toApiLocale(locale) } });
      router.replace(target);
    } catch (caught) {
      setError(errorMessage(t, caught));
      if (isTermsNotAccepted(caught)) {
        // A newer version took effect while the page was open: read and tick that one.
        setAgreed(false);
        void terms.refetch().catch(() => undefined);
      }
    }
  }

  if (!status.data && status.error && !status.loading) {
    // Offline or a server error: say so, with a way to try again or carry on limited.
    return (
      <div className={styles.panel} data-testid="terms-update-load-error">
        <h1 className={styles.title}>{t('termsUpdate.title')}</h1>
        <p className={styles.error} role="alert">
          {t('termsUpdate.unavailable')}
        </p>
        <div className={styles.actions}>
          <Button
            fullWidth
            onClick={() => void status.refetch().catch(() => undefined)}
            data-testid="terms-update-status-retry"
          >
            {t('termsUpdate.retry')}
          </Button>
          <Button
            variant="secondary"
            fullWidth
            onClick={() => router.replace(target)}
            data-testid="terms-update-not-now"
          >
            {t('termsUpdate.notNow')}
          </Button>
        </div>
      </div>
    );
  }

  if (!viewer?.mustAcceptTerms)
    return <p role="status" aria-busy="true" className={styles.muted} />;

  const unavailable = !terms.loading && !document;
  const termsHref = legalPath(document?.kind === 'HELPER_TERMS' ? 'helper-terms' : 'terms', locale);
  // The Privacy notice until account deletion exists (CLOSE_ACCOUNT_PAGE).
  const closeHref = legalPath(CLOSE_ACCOUNT_PAGE, locale);
  const shown = document
    ? {
        kind: document.kind,
        version: document.version,
        date: formatDate(String(document.effectiveAt), locale, 'long'),
      }
    : null;

  return (
    <form
      className={styles.panel}
      data-testid="terms-update"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <h1 className={styles.title}>{t('termsUpdate.title')}</h1>
      {shown ? (
        <p className={styles.muted} data-testid="terms-update-version">
          {t('termsUpdate.version', shown)}
        </p>
      ) : null}
      <section aria-labelledby="terms-update-changes">
        <ul className={own.list}>
          <li>
            <h2 id="terms-update-changes" className={own.heading}>
              {t('termsUpdate.whatsChanged')}
            </h2>
          </li>
          {/* NEEDS REVIEW (lawyer): the per-version summary of changes is legal text and
              comes from the lawyer with BEAV-182; until then a marked draft. The bookings
              and refusal lines are DRAFT legal sentences too (BEAV-39). */}
          <li className={styles.text}>
            {t('termsUpdate.summaryDraft', { kind: shown?.kind ?? 'TERMS' })}
          </li>
          {shown ? (
            <li className={styles.text} data-testid="terms-update-bookings">
              {t('termsUpdate.bookingsKeep', shown)}
            </li>
          ) : null}
        </ul>
      </section>
      <p className={styles.text} data-testid="terms-update-refuse">
        {t.rich('termsUpdate.refuse', {
          close: (chunks) => (
            <a href={closeHref} target="_blank" rel="noopener" data-testid="terms-update-close">
              {chunks}
            </a>
          ),
        })}
      </p>
      <a
        className={own.link}
        href={termsHref}
        target="_blank"
        rel="noopener"
        data-testid="terms-update-read"
      >
        {t('termsUpdate.readFull', { kind: shown?.kind ?? 'TERMS' })}
      </a>
      {/* Only once the document is loaded: the line names its version and date (R1-1). */}
      {shown ? (
        <label className={styles.checkRow}>
          <input
            type="checkbox"
            required
            checked={agreed}
            onChange={(event) => setAgreed(event.target.checked)}
            data-testid="terms-update-agree"
          />
          <span>
            {t.rich('termsUpdate.agree', {
              ...shown,
              terms: (chunks) => (
                <a href={termsHref} target="_blank" rel="noopener">
                  {chunks}
                </a>
              ),
            })}
          </span>
        </label>
      ) : null}
      {error ? (
        <p className={styles.error} role="alert">
          {error}
        </p>
      ) : null}
      <div className={styles.actions}>
        <Button
          type="submit"
          fullWidth
          disabled={!document || !agreed || busy}
          data-testid="terms-update-accept"
        >
          {t('termsUpdate.accept')}
        </Button>
        {unavailable ? (
          <>
            <p className={styles.muted} role="status">
              {t('termsUpdate.unavailable')}
            </p>
            <Button
              type="button"
              variant="secondary"
              fullWidth
              onClick={() => void terms.refetch().catch(() => undefined)}
              data-testid="terms-update-retry"
            >
              {t('termsUpdate.retry')}
            </Button>
          </>
        ) : !agreed ? (
          <p className={styles.muted} style={{ textAlign: 'center' }}>
            {t('termsUpdate.tickToAccept')}
          </p>
        ) : null}
        <Button
          type="button"
          variant="secondary"
          fullWidth
          onClick={() => router.replace(target)}
          data-testid="terms-update-not-now"
        >
          {t('termsUpdate.notNow')}
        </Button>
      </div>
    </form>
  );
}

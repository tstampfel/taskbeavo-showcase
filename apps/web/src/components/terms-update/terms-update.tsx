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
import { Button, Card, CheckIcon, PinnedFoot } from '../ui';
import { markTermsUpdateShown } from './shown';
import styles from './terms-update.module.css';

/**
 * "We've updated our terms" (mockup 21b). Shown while `viewer.mustAcceptTerms`: the account is
 * in limited mode until it accepts `viewer.termsToAccept`, in the page's language, with an
 * unticked box that names its version and effective date: what the API records (BEAV-188).
 * The API checks the version (TERMS_NOT_ACCEPTED if it changed meanwhile); "Not now" leaves
 * the account limited. Checkout never counts as accepting (user decision 2026-11-05). A
 * HELPER_TERMS document is named and linked as the Helper terms. Laid out as mockup 21b, as on
 * mobile (BEAV-215): the "What's changed" card with ticked lines, the full-terms link, the
 * refusal line and the tick box, then Accept with a small "Not now" in a pinned button bar.
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
      <div className={styles.page} data-testid="terms-update-load-error">
        <p className={styles.error} role="alert">
          {t('termsUpdate.unavailable')}
        </p>
        <PinnedFoot testId="terms-update-foot">
          <Button
            fullWidth
            onClick={() => void status.refetch().catch(() => undefined)}
            data-testid="terms-update-status-retry"
          >
            {t('termsUpdate.retry')}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            fullWidth
            onClick={() => router.replace(target)}
            data-testid="terms-update-not-now"
          >
            {t('termsUpdate.notNow')}
          </Button>
        </PinnedFoot>
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
      className={styles.page}
      data-testid="terms-update"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      {shown ? (
        <p className={styles.muted} data-testid="terms-update-version">
          {t('termsUpdate.version', shown)}
        </p>
      ) : null}
      <Card className={styles.changes}>
        <h2 id="terms-update-changes" className={styles.caps}>
          {t('termsUpdate.whatsChanged')}
        </h2>
        <ul className={styles.ticks}>
          {/* NEEDS REVIEW (lawyer): the per-version summary of changes is legal text and
              comes from the lawyer with BEAV-182; until then a marked draft. The bookings
              and refusal lines are DRAFT legal sentences too (BEAV-39). */}
          <Ticked>{t('termsUpdate.summaryDraft', { kind: shown?.kind ?? 'TERMS' })}</Ticked>
          {shown ? (
            <Ticked testId="terms-update-bookings">{t('termsUpdate.bookingsKeep', shown)}</Ticked>
          ) : null}
        </ul>
      </Card>
      <a
        className={styles.link}
        href={termsHref}
        target="_blank"
        rel="noopener"
        data-testid="terms-update-read"
      >
        {t('termsUpdate.readFull', { kind: shown?.kind ?? 'TERMS' })}
      </a>
      <p className={styles.text} data-testid="terms-update-refuse">
        {t.rich('termsUpdate.refuse', {
          close: (chunks) => (
            <a
              className={styles.inlineLink}
              href={closeHref}
              target="_blank"
              rel="noopener"
              data-testid="terms-update-close"
            >
              {chunks}
            </a>
          ),
        })}
      </p>
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
                <a className={styles.inlineLink} href={termsHref} target="_blank" rel="noopener">
                  {chunks}
                </a>
              ),
            })}
          </span>
        </label>
      ) : null}
      <PinnedFoot testId="terms-update-foot">
        {error ? (
          <p className={styles.error} role="alert">
            {error}
          </p>
        ) : null}
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
            <p className={`${styles.muted} ${styles.center}`} role="status">
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
          <p className={`${styles.muted} ${styles.center}`}>{t('termsUpdate.tickToAccept')}</p>
        ) : null}
        <Button
          type="button"
          variant="ghost"
          size="sm"
          fullWidth
          onClick={() => router.replace(target)}
          data-testid="terms-update-not-now"
        >
          {t('termsUpdate.notNow')}
        </Button>
      </PinnedFoot>
    </form>
  );
}

/** A line of the "What's changed" card, after a green tick (21b). */
function Ticked({ children, testId }: { children: string; testId?: string }) {
  return (
    <li className={styles.tick} data-testid={testId}>
      <span className={styles.tickIcon}>
        <CheckIcon size={18} />
      </span>
      <span>{children}</span>
    </li>
  );
}

'use client';

import { useApolloClient, useMutation } from '@apollo/client/react';
import { SignOutEverywhereFromEmailDocument } from '@taskbeavo/graphql';
import { useTranslations } from 'next-intl';
import { useState } from 'react';
import { Link } from '../../i18n/navigation';
import { Button, Card } from '../ui';
import { errorMessage } from './error-message';
import styles from './sign-in.module.css';

/** Confirm "This wasn't me": logs the account of the email's link out of all devices. */
export function ThisWasntMe({ token }: { token: string | null }) {
  const t = useTranslations();
  const client = useApolloClient();
  const [signOutEverywhere, { loading }] = useMutation(SignOutEverywhereFromEmailDocument);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (done) {
    return (
      <div className={styles.panel} data-testid="not-me-done">
        <h1 className={styles.title}>{t('sessions.notMeDoneTitle')}</h1>
        <p className={styles.muted}>{t('sessions.notMeDoneBody')}</p>
        <Link href="/sign-in" className={styles.textLink} data-testid="not-me-sign-in">
          {t('signIn.signIn')}
        </Link>
      </div>
    );
  }

  return (
    <div className={styles.panel} data-testid="not-me">
      <h1 className={styles.title}>{t('sessions.notMeTitle')}</h1>
      <Card className={styles.notMeCard}>
        <p className={styles.text}>{t('sessions.notMeBody')}</p>
        {token === null ? (
          <p className={styles.error} role="alert">
            {t('sessions.notMeMissingLink')}
          </p>
        ) : (
          <>
            {error ? (
              <p className={styles.error} role="alert" data-testid="not-me-error">
                {error}
              </p>
            ) : null}
            <Button
              variant="danger"
              fullWidth
              disabled={loading}
              data-testid="not-me-confirm"
              onClick={async () => {
                setError(null);
                try {
                  await signOutEverywhere({ variables: { token } });
                } catch (caught) {
                  setError(errorMessage(t, caught));
                  return;
                }
                // This browser may have been signed in to the account too.
                await client.resetStore().catch(() => undefined);
                setDone(true);
              }}
            >
              {t('sessions.signOutEverywhere')}
            </Button>
          </>
        )}
      </Card>
    </div>
  );
}

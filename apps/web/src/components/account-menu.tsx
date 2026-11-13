'use client';

import { useApolloClient, useQuery } from '@apollo/client/react';
import { ViewerDocument } from '@taskbeavo/graphql';
import { useTranslations } from 'next-intl';
import { useState } from 'react';
import { createAuthClient } from '../auth/auth-client';
import { Link, usePathname, useRouter } from '../i18n/navigation';
import { Button } from './ui';

const auth = createAuthClient();

/**
 * "Log in" while signed out; the user's name, a link to Settings, "Sign out" and "Log out of
 * all devices" while signed in.
 */
export function AccountMenu() {
  const t = useTranslations('signIn');
  const tSessions = useTranslations('sessions');
  const tSettings = useTranslations('settings');
  const client = useApolloClient();
  const router = useRouter();
  const pathname = usePathname();
  const { data } = useQuery(ViewerDocument);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const viewer = data?.viewer;

  async function signedOut() {
    // Nothing of the account stays in this tab; the viewer query refetches signed out.
    await client.resetStore().catch(() => undefined);
    setBusy(false);
    router.replace('/');
  }

  if (!viewer) {
    if (pathname === '/sign-in') return null;
    return (
      <Link
        href={pathname === '/' ? '/sign-in' : { pathname: '/sign-in', query: { next: pathname } }}
        data-testid="sign-in-link"
        style={{ display: 'inline-flex', alignItems: 'center', minHeight: 44, fontWeight: 700 }}
      >
        {t('signIn')}
      </Link>
    );
  }

  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 12 }}>
      <span data-testid="signed-in-as">{t('signedInAs', { name: viewer.firstName })}</span>
      <Link
        href="/settings"
        data-testid="settings-link"
        style={{ display: 'inline-flex', alignItems: 'center', minHeight: 44, fontWeight: 700 }}
      >
        {tSettings('title')}
      </Link>
      <Button
        variant="secondary"
        size="sm"
        disabled={busy}
        data-testid="sign-out"
        onClick={async () => {
          setBusy(true);
          await auth.signOut();
          await signedOut();
        }}
      >
        {t('signOut')}
      </Button>
      <Button
        variant="secondary"
        size="sm"
        disabled={busy}
        data-testid="sign-out-everywhere"
        onClick={async () => {
          setBusy(true);
          setFailed(false);
          try {
            await auth.signOutEverywhere();
          } catch {
            setFailed(true);
            setBusy(false);
            return;
          }
          await signedOut();
        }}
      >
        {tSessions('signOutEverywhere')}
      </Button>
      {failed ? (
        <span role="alert" data-testid="sign-out-everywhere-failed">
          {tSessions('signOutEverywhereFailed')}
        </span>
      ) : null}
    </div>
  );
}

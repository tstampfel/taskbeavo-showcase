'use client';

import { useApolloClient, useQuery } from '@apollo/client/react';
import { ViewerDocument } from '@taskbeavo/graphql';
import { useLocale, useTranslations } from 'next-intl';
import { useEffect, useId, useRef, useState } from 'react';
import { createAuthClient } from '../auth/auth-client';
import { Link, usePathname, useRouter } from '../i18n/navigation';
import { LanguageSwitcher } from './language-switcher';
import styles from './shell/shell.module.css';
import { Button } from './ui';

const auth = createAuthClient();

export interface AccountMenuProps {
  /**
   * Put the language switcher inside the menu (the app shell's top bar). Public pages show the
   * switcher next to the menu instead, and a plain "Log in" link while signed out.
   */
  withLanguage?: boolean;
}

/**
 * One 44px button that opens the account menu: who is signed in, Settings, the language
 * switcher (`withLanguage`), "Sign out" and "Log out of all devices". Signed out it offers
 * "Log in" instead. Escape, a click outside, tabbing out or opening another page closes it.
 */
export function AccountMenu({ withLanguage = false }: AccountMenuProps) {
  const t = useTranslations('signIn');
  const tShell = useTranslations('appShell');
  const tSessions = useTranslations('sessions');
  const tSettings = useTranslations('settings');
  const client = useApolloClient();
  const router = useRouter();
  const pathname = usePathname();
  const locale = useLocale();
  const { data } = useQuery(ViewerDocument);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const panelId = useId();
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const viewer = data?.viewer;

  // Another page or language closes the menu.
  const place = `${locale}:${pathname}`;
  const [shownAt, setShownAt] = useState(place);
  if (shownAt !== place) {
    setShownAt(place);
    setOpen(false);
  }

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: PointerEvent) {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== 'Escape') return;
      setOpen(false);
      button.current?.focus();
    }
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  const signInHref =
    pathname === '/' ? '/sign-in' : { pathname: '/sign-in', query: { next: pathname } };

  if (!viewer && !withLanguage) {
    if (pathname === '/sign-in') return null;
    return (
      <Link href={signInHref} data-testid="sign-in-link" className={styles.textLink}>
        {t('signIn')}
      </Link>
    );
  }

  async function signedOut() {
    // Nothing of the account stays in this tab; the viewer query refetches signed out.
    await client.resetStore().catch(() => undefined);
    setBusy(false);
    setOpen(false);
    router.replace('/');
  }

  return (
    <div
      ref={root}
      className={styles.menuRoot}
      onBlur={(event) => {
        // Tabbing past the last item closes the menu. Focus that goes nowhere (a click on
        // menu text, or on a button in Safari) leaves it open; outside taps close it above.
        const next = event.relatedTarget;
        if (open && next instanceof Node && !root.current?.contains(next)) setOpen(false);
      }}
    >
      <button
        ref={button}
        type="button"
        className={styles.iconButton}
        aria-label={tShell('accountMenu')}
        aria-expanded={open}
        aria-controls={panelId}
        data-testid="account-menu-button"
        onClick={() => setOpen((value) => !value)}
      >
        <PersonIcon />
      </button>
      <div id={panelId} className={styles.menu} hidden={!open} data-testid="account-menu">
        {viewer ? (
          <>
            <p className={styles.menuWho} data-testid="signed-in-as">
              {t('signedInAs', { name: viewer.firstName })}
            </p>
            <Link href="/settings" data-testid="settings-link" className={styles.menuLink}>
              {tSettings('title')}
            </Link>
          </>
        ) : pathname === '/sign-in' ? null : (
          <Link href={signInHref} data-testid="sign-in-link" className={styles.menuLink}>
            {t('signIn')}
          </Link>
        )}
        {withLanguage ? <LanguageSwitcher /> : null}
        {viewer ? (
          <>
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
              <p role="alert" className={styles.menuError} data-testid="sign-out-everywhere-failed">
                {tSessions('signOutEverywhereFailed')}
              </p>
            ) : null}
          </>
        ) : null}
      </div>
    </div>
  );
}

function PersonIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <circle cx="12" cy="8" r="4" fill="none" stroke="currentColor" strokeWidth="2" />
      <path
        d="M4 20c0-4 3.6-6 8-6s8 2 8 6"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  );
}

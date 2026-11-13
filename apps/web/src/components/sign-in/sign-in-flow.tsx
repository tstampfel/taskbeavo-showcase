'use client';

import { useApolloClient, useMutation } from '@apollo/client/react';
import {
  EMAIL_RESEND_SECONDS,
  RequestEmailCodeDocument,
  RequestSignInNonceDocument,
  SetLocaleDocument,
  accountDetailsInput,
  isPlausibleEmail,
  needsPhoneVerification,
  stepAfterSignIn,
  type SignInAnswer,
  type SignInProvider,
  type SignInStep,
  type ViewerFieldsFragment,
} from '@taskbeavo/graphql';
import {
  defaultCountry,
  languageNames,
  signInLocaleDecision,
  toApiLocale,
  type Locale,
} from '@taskbeavo/i18n';
import { useLocale, useTranslations } from 'next-intl';
import { useEffect, useRef, useState } from 'react';
import { createAuthClient, safeNextPath } from '../../auth/auth-client';
import { renderGoogleButton, signInWithApple, webProviderConfig } from '../../auth/providers';
import { useRouter } from '../../i18n/navigation';
import { loadPickedLocale, savePickedLocale } from '../../i18n/picked-locale';
import { Button, Input } from '../ui';
import { AccountDetailsForm } from './account-details-form';
import { CodeStep } from './code-step';
import { errorMessage } from './error-message';
import { watchNonceExpiry } from './nonce-expiry';
import styles from './sign-in.module.css';

type Screen =
  | { kind: 'start' }
  | { kind: 'emailCode'; email: string }
  | Exclude<SignInStep, { kind: 'signedIn' }>
  | { kind: 'localePrompt'; user: ViewerFieldsFragment; picked: Locale; account: Locale };

const auth = createAuthClient();
const providers = webProviderConfig();

function deviceTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || 'Europe/Zagreb';
}

/**
 * Log in or sign up (mockup 15j, with the steps of 15b to 15e2): Google, Apple or an email
 * code, then the account-details step for a new account. The /api/auth route handlers keep
 * the tokens in httpOnly cookies; this page only ever sees the signed-in user.
 */
export function SignInFlow({ next }: { next: string | null }) {
  const t = useTranslations();
  const locale = useLocale() as Locale;
  const router = useRouter();
  const client = useApolloClient();
  const [screen, setScreen] = useState<Screen>({ kind: 'start' });
  const [resendAt, setResendAt] = useState<number | null>(null);
  const [requestEmailCode] = useMutation(RequestEmailCodeDocument);
  const [setLocale] = useMutation(SetLocaleDocument);
  const target = safeNextPath(next);

  async function sendEmailCode(email: string) {
    await requestEmailCode({ variables: { email, locale: toApiLocale(locale) } });
    setResendAt(Date.now() + EMAIL_RESEND_SECONDS * 1000);
  }

  function go(user: ViewerFieldsFragment, to: Locale) {
    // The phone step comes next for an unverified number, then the page the user came for.
    const pathname = needsPhoneVerification(user) ? '/verify-phone' : target;
    const query = pathname === '/verify-phone' && target !== '/' ? { next: target } : undefined;
    // The cookie the handler set only steers `/`; the next page must open in the account's
    // language too, so it goes in the URL.
    router.replace(query ? { pathname, query } : pathname, { locale: to });
  }

  async function signedIn(user: ViewerFieldsFragment) {
    // The page's cache was filled while signed out.
    await client.clearStore();
    const decision = signInLocaleDecision(user.locale, loadPickedLocale());
    if (decision.kind === 'useAccount') {
      savePickedLocale(null);
      go(user, decision.locale);
    } else {
      setScreen({
        kind: 'localePrompt',
        user,
        picked: decision.picked,
        account: decision.account,
      });
    }
  }

  async function handle(answer: SignInAnswer, provider?: SignInProvider) {
    const step = stepAfterSignIn(answer, provider);
    if (step.kind === 'signedIn') await signedIn(step.user);
    else setScreen(step);
  }

  if (screen.kind === 'emailCode') {
    return (
      <CodeStep
        testId="email-code"
        title={t('signIn.codeTitle')}
        intro={
          <>
            {t('signIn.codeSentTo', { email: screen.email })} ·{' '}
            <button
              type="button"
              className={styles.linkButton}
              onClick={() => setScreen({ kind: 'start' })}
            >
              {t('signIn.wrongEmail')}
            </button>
          </>
        }
        resendAt={resendAt}
        onResend={() => sendEmailCode(screen.email)}
        onSubmit={async (code) => handle(await auth.verifyEmailCode(screen.email, code))}
      />
    );
  }

  if (screen.kind === 'linkCode') {
    return (
      <CodeStep
        testId="link-code"
        title={t('signIn.linkHeader')}
        intro={
          <>
            {t('signIn.linkBody', { email: screen.email, provider: screen.provider })}{' '}
            <button
              type="button"
              className={styles.linkButton}
              onClick={() => setScreen({ kind: 'start' })}
            >
              {t('signIn.back')}
            </button>
          </>
        }
        onSubmit={async (code) => {
          const session = await auth.linkProvider(screen.ticket, code);
          await signedIn(session.user);
        }}
      />
    );
  }

  if (screen.kind === 'details') {
    return (
      <AccountDetailsForm
        email={screen.email}
        provider={screen.provider}
        initial={{
          firstName: screen.firstName,
          lastName: screen.lastName,
          country: defaultCountry(typeof navigator === 'undefined' ? [] : navigator.languages),
        }}
        onSubmit={async (form) => {
          const session = await auth.completeAccountDetails(
            accountDetailsInput(screen.ticket, form, deviceTimeZone()),
          );
          await signedIn(session.user);
        }}
      />
    );
  }

  if (screen.kind === 'localePrompt') {
    return (
      <LocalePrompt
        picked={screen.picked}
        account={screen.account}
        onAnswer={async (yes) => {
          if (yes) {
            // The account follows the pick; failing that, it keeps its language.
            await setLocale({ variables: { locale: toApiLocale(screen.picked) } }).catch(
              () => undefined,
            );
          }
          go(screen.user, screen.picked);
        }}
      />
    );
  }

  return (
    <StartStep
      onEmail={async (email) => {
        await sendEmailCode(email);
        setScreen({ kind: 'emailCode', email });
      }}
      onProvider={handle}
    />
  );
}

function StartStep({
  onEmail,
  onProvider,
}: {
  onEmail(email: string): Promise<void>;
  onProvider(answer: SignInAnswer, provider: SignInProvider): Promise<void>;
}) {
  const t = useTranslations();
  const locale = useLocale() as Locale;
  const [email, setEmail] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [requestNonce] = useMutation(RequestSignInNonceDocument);

  async function nonce(): Promise<string> {
    const { data } = await requestNonce();
    if (!data) throw new Error('No nonce');
    return data.requestSignInNonce.nonce;
  }

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (caught) {
      setError(errorMessage(t, caught));
    } finally {
      setBusy(false);
    }
  }

  async function apple() {
    const servicesId = providers.appleServicesId;
    if (!servicesId) return;
    await run(async () => {
      const issued = await nonce();
      const result = await signInWithApple({
        servicesId,
        nonce: issued,
        locale,
        redirectUri: `${window.location.origin}/${locale}/sign-in`,
      });
      if (!result) return;
      const answer = await auth.signInWithProvider({ provider: 'APPLE', nonce: issued, ...result });
      await onProvider(answer, 'APPLE');
    });
  }

  return (
    <div className={styles.panel} data-testid="sign-in-start">
      <h1 className={styles.title}>{t('signIn.title')}</h1>
      {providers.appleServicesId ? (
        <Button
          variant="primary"
          fullWidth
          className={styles.appleButton}
          disabled={busy}
          onClick={() => void apple()}
          data-testid="apple-sign-in"
        >
          {t('signIn.continueWithApple')}
        </Button>
      ) : null}
      {providers.googleClientId ? (
        <GoogleButton
          clientId={providers.googleClientId}
          nonce={nonce}
          onIdToken={(idToken, issued) =>
            run(async () => {
              const answer = await auth.signInWithProvider({
                provider: 'GOOGLE',
                idToken,
                nonce: issued,
              });
              await onProvider(answer, 'GOOGLE');
            })
          }
          onError={(caught) => setError(errorMessage(t, caught))}
        />
      ) : null}
      {providers.appleServicesId || providers.googleClientId ? (
        <div className={styles.divider}>{t('signIn.or')}</div>
      ) : null}
      <form
        className={styles.form}
        // Our translated message instead of the browser's, which is in the browser's language.
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          const address = email.trim();
          if (!isPlausibleEmail(address)) {
            setError(t('signIn.emailInvalid'));
            return;
          }
          void run(() => onEmail(address));
        }}
      >
        <Input
          label={t('signIn.emailLabel')}
          type="email"
          autoComplete="email"
          inputMode="email"
          placeholder={t('signIn.emailPlaceholder')}
          hint={t('signIn.emailHint')}
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          data-testid="email"
        />
        {error ? (
          <p className={styles.error} role="alert">
            {error}
          </p>
        ) : null}
        <Button type="submit" fullWidth disabled={busy} data-testid="send-code">
          {t('signIn.sendCode')}
        </Button>
      </form>
      <p className={styles.muted}>{t('signIn.newHere')}</p>
    </div>
  );
}

/**
 * Google's own button. Each render gets a fresh nonce, since the API burns one per sign-in;
 * after a sign-in attempt the button is drawn again with a new one. The nonce is baked into the
 * button, so on a page left open it is also replaced a minute before the API would let it
 * expire, timed from when it arrived (or when the tab comes back after that).
 */
function GoogleButton({
  clientId,
  nonce,
  onIdToken,
  onError,
}: {
  clientId: string;
  nonce(): Promise<string>;
  onIdToken(idToken: string, nonce: string): Promise<void>;
  onError(error: unknown): void;
}) {
  const locale = useLocale() as Locale;
  const parent = useRef<HTMLDivElement>(null);
  const [round, setRound] = useState(0);
  const latest = useRef({ nonce, onIdToken, onError });
  latest.current = { nonce, onIdToken, onError };

  useEffect(() => {
    let cancelled = false;
    let stopWatching: () => void = () => undefined;
    void (async () => {
      try {
        const issued = await latest.current.nonce();
        if (cancelled || !parent.current) return;
        stopWatching = watchNonceExpiry(() => setRound((r) => r + 1));
        await renderGoogleButton(parent.current, {
          clientId,
          nonce: issued,
          locale,
          onIdToken: (idToken) => {
            void latest.current.onIdToken(idToken, issued).finally(() => setRound((r) => r + 1));
          },
        });
      } catch (caught) {
        if (!cancelled) latest.current.onError(caught);
      }
    })();
    return () => {
      cancelled = true;
      stopWatching();
    };
  }, [clientId, locale, round]);

  return <div ref={parent} className={styles.providerButton} data-testid="google-sign-in" />;
}

function LocalePrompt({
  picked,
  account,
  onAnswer,
}: {
  picked: Locale;
  account: Locale;
  onAnswer(yes: boolean): Promise<void>;
}) {
  const t = useTranslations();
  const [busy, setBusy] = useState(false);
  const answer = (yes: boolean) => {
    setBusy(true);
    void onAnswer(yes);
  };
  return (
    <div className={styles.panel} data-testid="locale-prompt">
      <h1 className={styles.title}>
        {t('signIn.localePromptTitle', { language: languageNames[picked] })}
      </h1>
      <p className={styles.muted}>
        {t('signIn.localePromptBody', { account: languageNames[account] })}
      </p>
      <div className={styles.actions}>
        <Button fullWidth disabled={busy} onClick={() => answer(true)} data-testid="locale-yes">
          {t('signIn.localePromptYes')}
        </Button>
        <Button
          variant="secondary"
          fullWidth
          disabled={busy}
          onClick={() => answer(false)}
          data-testid="locale-no"
        >
          {t('signIn.localePromptNo')}
        </Button>
      </div>
    </div>
  );
}

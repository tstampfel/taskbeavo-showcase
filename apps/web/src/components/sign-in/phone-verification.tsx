'use client';

import { useMutation, useQuery } from '@apollo/client/react';
import {
  RequestPhoneCodeDocument,
  VerifyPhoneCodeDocument,
  ViewerDocument,
  type RequestPhoneCodeMutation,
} from '@taskbeavo/graphql';
import { phonePrefix } from '@taskbeavo/i18n';
import { useTranslations } from 'next-intl';
import { useEffect, useState } from 'react';
import { safeNextPath } from '../../auth/auth-client';
import { useRouter } from '../../i18n/navigation';
import { Button, Input } from '../ui';
import { CodeStep } from './code-step';
import { errorMessage } from './error-message';
import styles from './sign-in.module.css';

/** The code just sent, with the number as typed (kept in memory only, to send a new code). */
type Sent = RequestPhoneCodeMutation['requestPhoneCode'] & { phone: string };

/**
 * Verify your phone (mockups 15f and 15g). Posting, offering and chat need a verified mobile
 * number; the API refuses numbers it can't text yet (+1 countries) with
 * PHONE_COUNTRY_NOT_SUPPORTED, shown as its own message. Only the masked number comes back.
 */
export function PhoneVerification({ next }: { next: string | null }) {
  const t = useTranslations();
  const router = useRouter();
  const target = safeNextPath(next);
  const { data, loading, error, refetch } = useQuery(ViewerDocument, {
    fetchPolicy: 'network-only',
    notifyOnNetworkStatusChange: true,
  });
  const viewer = data?.viewer;
  const [sent, setSent] = useState<Sent | null>(null);

  useEffect(() => {
    if (!loading && data && !viewer) {
      router.replace({ pathname: '/sign-in', query: { next: '/verify-phone' } });
    }
  }, [loading, data, viewer, router]);

  if (!viewer && error && !loading) {
    // Offline or a server error: say so, instead of waiting on a viewer that never comes.
    return (
      <div className={styles.panel} data-testid="phone-load-error">
        <p className={styles.error} role="alert">
          {errorMessage(t, error)}
        </p>
        <Button
          fullWidth
          onClick={() => void refetch().catch(() => undefined)}
          data-testid="phone-retry"
        >
          {t('errorBoundary.retry')}
        </Button>
      </div>
    );
  }

  if (!viewer) return <p role="status" aria-busy="true" className={styles.muted} />;

  if (viewer.phoneVerified) {
    return (
      <div className={styles.panel} data-testid="phone-verified">
        <p className={styles.note} role="status">
          {t('phone.verified')}
        </p>
        <Button fullWidth onClick={() => router.replace(target)} data-testid="phone-continue">
          {t('signIn.verifyCode')}
        </Button>
      </div>
    );
  }

  if (sent) {
    return (
      <PhoneCode
        sent={sent}
        onChange={() => setSent(null)}
        onResent={setSent}
        onVerified={() => router.replace(target)}
      />
    );
  }

  return (
    <PhoneNumber country={viewer.country} onSent={setSent} onLater={() => router.replace(target)} />
  );
}

function PhoneNumber({
  country,
  onSent,
  onLater,
}: {
  country: string;
  onSent(sent: Sent): void;
  onLater(): void;
}) {
  const t = useTranslations();
  const [phone, setPhone] = useState(() => phonePrefix(country));
  const [error, setError] = useState<string | null>(null);
  const [requestPhoneCode, { loading }] = useMutation(RequestPhoneCodeDocument);

  return (
    <form
      className={styles.panel}
      data-testid="phone-number"
      onSubmit={(event) => {
        event.preventDefault();
        setError(null);
        const typed = phone.trim();
        requestPhoneCode({ variables: { phone: typed } }).then(
          ({ data }) => data && onSent({ ...data.requestPhoneCode, phone: typed }),
          (caught: unknown) => setError(errorMessage(t, caught)),
        );
      }}
    >
      <h2 className={styles.title}>{t('phone.title')}</h2>
      <p className={styles.muted}>{t('phone.body')}</p>
      <Input
        label={t('phone.label')}
        type="tel"
        autoComplete="tel"
        inputMode="tel"
        hint={t('phone.hint')}
        error={error ?? undefined}
        value={phone}
        onChange={(event) => setPhone(event.target.value)}
        data-testid="phone-input"
      />
      <p className={styles.note}>{t('phone.privacy')}</p>
      <div className={styles.actions}>
        <Button type="submit" fullWidth disabled={loading} data-testid="send-sms">
          {t('phone.send')}
        </Button>
        <Button variant="ghost" fullWidth onClick={onLater} data-testid="phone-later">
          {t('phone.later')}
        </Button>
      </div>
    </form>
  );
}

function PhoneCode({
  sent,
  onChange,
  onResent,
  onVerified,
}: {
  sent: Sent;
  onChange(): void;
  onResent(sent: Sent): void;
  onVerified(): void;
}) {
  const t = useTranslations();
  const [verifyPhoneCode] = useMutation(VerifyPhoneCodeDocument);
  const [requestPhoneCode] = useMutation(RequestPhoneCodeDocument);
  const resendAt = new Date(String(sent.resendAvailableAt)).getTime();

  return (
    <CodeStep
      testId="phone-code"
      title={t('phone.codeTitle')}
      titleLevel={2}
      intro={
        <>
          {t('phone.sentTo', { phone: sent.phoneNumberMasked })} ·{' '}
          <button type="button" className={styles.linkButton} onClick={onChange}>
            {t('phone.change')}
          </button>
        </>
      }
      resendAt={Number.isNaN(resendAt) ? null : resendAt}
      onResend={async () => {
        const { data } = await requestPhoneCode({ variables: { phone: sent.phone } });
        if (data) onResent({ ...data.requestPhoneCode, phone: sent.phone });
      }}
      onSubmit={async (code) => {
        await verifyPhoneCode({ variables: { code } });
        onVerified();
      }}
    />
  );
}

'use client';

import { CODE_LENGTH, formatCountdown, normalizeCode, signInErrorOf } from '@taskbeavo/graphql';
import { useTranslations } from 'next-intl';
import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { Button } from '../ui';
import { errorMessage } from './error-message';
import styles from './sign-in.module.css';
import { useSecondsLeft } from './use-countdown';

export interface CodeStepProps {
  title: string;
  /** Where the code went, and how to change that. */
  intro: ReactNode;
  /** Checks the code; throws the API's error. */
  onSubmit(code: string): Promise<void>;
  /** Sends a new code; omitted when there is no resend (linking a provider). */
  onResend?: () => Promise<void>;
  /** When the resend timer ends (epoch ms). */
  resendAt?: number | null;
  testId: string;
}

/**
 * Six-digit code entry (mockups 15c, 15d and 15g). The code goes in as soon as all six
 * digits are there; a wrong one says so, and too many tries show the lockout with its timer.
 */
export function CodeStep({
  title,
  intro,
  onSubmit,
  onResend,
  resendAt = null,
  testId,
}: CodeStepProps) {
  const t = useTranslations();
  const inputId = useId();
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // A second tap while a code is on its way would send (and, for SMS, pay for) another one.
  const [resending, setResending] = useState(false);
  const [lockedUntil, setLockedUntil] = useState<number | null>(null);
  const lockedSeconds = useSecondsLeft(lockedUntil);
  const resendSeconds = useSecondsLeft(resendAt);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => input.current?.focus(), []);

  async function submit(value: string) {
    if (value.length !== CODE_LENGTH || busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await onSubmit(value);
    } catch (caught) {
      const { code: errorCode, retryAfterSeconds } = signInErrorOf(caught);
      if (
        (errorCode === 'RATE_LIMITED' || errorCode === 'LOCKED') &&
        retryAfterSeconds !== undefined
      ) {
        setLockedUntil(Date.now() + retryAfterSeconds * 1000);
      } else {
        setError(errorMessage(t, caught));
      }
      setCode('');
      input.current?.focus();
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    if (!onResend || resending) return;
    setResending(true);
    setError(null);
    setNotice(null);
    try {
      await onResend();
      setNotice(t('signIn.codeResent'));
    } catch (caught) {
      setError(errorMessage(t, caught));
    } finally {
      setResending(false);
    }
  }

  if (lockedSeconds > 0) {
    return (
      <div className={styles.panel} data-testid={`${testId}-locked`}>
        <h1 className={styles.title}>{title}</h1>
        <div className={styles.card} role="alert">
          <h2 className={styles.cardTitle}>{t('signIn.lockedTitle')}</h2>
          <p className={styles.muted}>
            {t('signIn.lockedBody', { time: formatCountdown(lockedSeconds) })}
          </p>
        </div>
      </div>
    );
  }

  return (
    <form
      className={styles.panel}
      data-testid={testId}
      onSubmit={(event) => {
        event.preventDefault();
        void submit(code);
      }}
    >
      <h1 className={styles.title}>{title}</h1>
      <div className={styles.muted}>{intro}</div>
      <label htmlFor={inputId} className={styles.label}>
        {t('signIn.codeLabel')}
      </label>
      <input
        ref={input}
        id={inputId}
        className={styles.codeInput}
        data-testid={`${testId}-input`}
        inputMode="numeric"
        autoComplete="one-time-code"
        pattern="[0-9]*"
        maxLength={CODE_LENGTH}
        value={code}
        disabled={busy}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${inputId}-error` : undefined}
        onChange={(event) => {
          const value = normalizeCode(event.target.value);
          setCode(value);
          if (value.length === CODE_LENGTH) void submit(value);
        }}
      />
      {error ? (
        <p id={`${inputId}-error`} className={styles.error} role="alert">
          {error}
        </p>
      ) : null}
      {notice ? (
        <p className={styles.muted} role="status">
          {notice}
        </p>
      ) : null}
      <Button type="submit" fullWidth disabled={busy || code.length !== CODE_LENGTH}>
        {t('signIn.verifyCode')}
      </Button>
      {onResend ? (
        resendSeconds > 0 ? (
          <p className={styles.muted} aria-live="off">
            {t('signIn.resendIn', { time: formatCountdown(resendSeconds) })}
          </p>
        ) : (
          <button
            type="button"
            className={styles.linkButton}
            disabled={resending}
            aria-busy={resending || undefined}
            onClick={() => void resend()}
          >
            {t('signIn.resend')}
          </button>
        )
      ) : null}
    </form>
  );
}

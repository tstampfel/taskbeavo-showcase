import { formatCountdown, signInErrorOf } from '@taskbeavo/graphql';

type Translate = ((key: string, values?: Record<string, string>) => string) & {
  has(key: string): boolean;
};

/**
 * A failed call as translated text: the API's code as `errors.<CODE>` (a RATE_LIMITED with a
 * retry time, or a LOCKED one, says how long to wait), and a network failure as `signIn.networkError`. A code
 * the catalogues don't know yet (or a limit error from the API) gets the general message too,
 * never the raw key.
 */
export function errorMessage(t: Translate, error: unknown): string {
  const { code, retryAfterSeconds } = signInErrorOf(error);
  if ((code === 'RATE_LIMITED' || code === 'LOCKED') && retryAfterSeconds !== undefined) {
    return t('signIn.tryAgainIn', { time: formatCountdown(retryAfterSeconds) });
  }
  const key = `errors.${code ?? ''}`;
  return code && t.has(key) ? t(`errors.${code}`) : t('signIn.networkError');
}

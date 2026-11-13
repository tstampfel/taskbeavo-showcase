import { formatCountdown, signInErrorOf } from '@taskbeavo/graphql';
import type { TFunction } from 'i18next';
import { i18n } from '../../i18n';

/**
 * A failed call as translated text: the API's code as `errors.<CODE>` (a RATE_LIMITED with a
 * retry time says how long to wait), and a network failure as `signIn.networkError`. A code
 * the catalogues don't know yet (or a limit error from the API) gets the general message too,
 * never the raw key.
 */
export function errorMessage(t: TFunction, error: unknown): string {
  const { code, retryAfterSeconds } = signInErrorOf(error);
  if ((code === 'RATE_LIMITED' || code === 'LOCKED') && retryAfterSeconds !== undefined) {
    return t('signIn.tryAgainIn', { time: formatCountdown(retryAfterSeconds) });
  }
  return code && i18n.exists(`errors.${code}`) ? t(`errors.${code}`) : t('signIn.networkError');
}

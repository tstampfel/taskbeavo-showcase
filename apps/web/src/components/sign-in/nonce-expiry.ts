/**
 * How long the API accepts a sign-in nonce (`SIGN_IN_NONCE_TTL_SECONDS`, 600 s by default).
 * The refresh is timed from when the browser received the nonce, never by comparing the
 * server's `expiresAt` with the browser clock: a device clock minutes off would put that time
 * in the past and re-issue nonces in a loop until `requestSignInNonce` is rate limited.
 */
export const NONCE_TTL_MS = 600_000;

/** A nonce is replaced this long before the API stops accepting it. */
export const NONCE_REFRESH_MARGIN_MS = 60_000;

/** Never replace a nonce sooner than this, whatever the TTL and margin say. */
export const NONCE_MIN_REFRESH_MS = 60_000;

/** How long after receiving a nonce to replace it. */
export function nonceRefreshDelay(ttl = NONCE_TTL_MS, margin = NONCE_REFRESH_MARGIN_MS): number {
  const delay = ttl - margin;
  return Number.isFinite(delay) ? Math.max(NONCE_MIN_REFRESH_MS, delay) : NONCE_MIN_REFRESH_MS;
}

export interface ExpiryEnv {
  now(): number;
  setTimeout(callback: () => void, ms: number): unknown;
  clearTimeout(handle: unknown): void;
  document: Pick<Document, 'addEventListener' | 'removeEventListener' | 'visibilityState'>;
}

const browser = (): ExpiryEnv => ({
  now: () => Date.now(),
  setTimeout: (callback, ms) => window.setTimeout(callback, ms),
  clearTimeout: (handle) => window.clearTimeout(handle as number),
  document,
});

/**
 * Calls `onDue` once, `delay` after now (call it as the nonce arrives). Browsers slow down
 * timers in background tabs, so the check runs again when the tab becomes visible. Returns the
 * cleanup.
 */
export function watchNonceExpiry(
  onDue: () => void,
  env: ExpiryEnv = browser(),
  delay = nonceRefreshDelay(),
): () => void {
  const refreshAt = env.now() + delay;
  let done = false;
  const due = () => {
    if (done) return;
    done = true;
    stop();
    onDue();
  };
  const timer = env.setTimeout(due, delay);
  const onVisible = () => {
    if (env.document.visibilityState === 'visible' && env.now() >= refreshAt) due();
  };
  env.document.addEventListener('visibilitychange', onVisible);
  function stop() {
    env.clearTimeout(timer);
    env.document.removeEventListener('visibilitychange', onVisible);
  }
  return () => {
    done = true;
    stop();
  };
}

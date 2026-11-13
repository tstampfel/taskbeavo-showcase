/**
 * This browser's answer to the cookie banner (BEAV-83). Remembering the choice is strictly
 * necessary, so the cookie itself needs no consent; it holds only the answer, the cookie
 * policy version it referred to and a random visitor id, which the CONSENT rows logged
 * before sign-in refer to. No personal data.
 */

export const CONSENT_COOKIE = 'tb_consent';
/** Ask again after six months, as the cookie banner guidance of EU regulators suggests. */
export const CONSENT_MAX_AGE_SECONDS = 180 * 24 * 60 * 60;
/** Fired on `window` to open the banner again: the footer's "Cookie settings". */
export const OPEN_CONSENT_EVENT = 'taskbeavo:open-consent';

export interface ConsentChoice {
  analytics: boolean;
  visitorId: string;
  /** The cookie policy version the choice referred to; null when none was in force. */
  version: string | null;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The choice in a `Cookie` header or `document.cookie`; null when absent or unreadable. */
export function parseConsent(cookies: string): ConsentChoice | null {
  const raw = cookies
    .split(';')
    .map((part) => part.trim())
    .find((part) => part.startsWith(`${CONSENT_COOKIE}=`))
    ?.slice(CONSENT_COOKIE.length + 1);
  if (!raw) return null;
  try {
    const value = JSON.parse(decodeURIComponent(raw)) as Partial<ConsentChoice>;
    if (typeof value.analytics !== 'boolean') return null;
    if (typeof value.visitorId !== 'string' || !UUID.test(value.visitorId)) return null;
    const version = typeof value.version === 'string' ? value.version : null;
    return { analytics: value.analytics, visitorId: value.visitorId, version };
  } catch {
    return null;
  }
}

/** The `document.cookie` assignment that stores `choice`. */
export function serializeConsent(choice: ConsentChoice, secure: boolean): string {
  const value = encodeURIComponent(JSON.stringify(choice));
  const flags = [`Path=/`, `Max-Age=${CONSENT_MAX_AGE_SECONDS}`, 'SameSite=Lax'];
  if (secure) flags.push('Secure');
  return `${CONSENT_COOKIE}=${value}; ${flags.join('; ')}`;
}

export function readConsent(): ConsentChoice | null {
  try {
    return parseConsent(document.cookie);
  } catch {
    return null;
  }
}

// A tiny store over the cookie for useSyncExternalStore: the banner, the footer link and
// Settings see the same answer, and every change reaches the analytics gate.
const listeners = new Set<() => void>();
let snapshot: ConsentChoice | null = null;
let snapshotKey = 'null';

export function subscribeConsent(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** The stored choice, the same object until it changes. */
export function consentSnapshot(): ConsentChoice | null {
  const current = readConsent();
  const key = JSON.stringify(current);
  if (key !== snapshotKey) {
    snapshotKey = key;
    snapshot = current;
  }
  return snapshot;
}

export function writeConsent(choice: ConsentChoice): void {
  document.cookie = serializeConsent(choice, window.location.protocol === 'https:');
  listeners.forEach((listener) => listener());
}

/** The browser's visitor id: the stored one, or a new random one for its first answer. */
export function visitorId(existing: ConsentChoice | null): string {
  return existing?.visitorId ?? crypto.randomUUID();
}

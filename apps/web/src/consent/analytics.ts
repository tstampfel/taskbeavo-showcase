/**
 * PostHog analytics, only after consent (BEAV-83; plan, GDPR section "Correction, objection
 * and consent"). Until the visitor agrees, posthog-js isn't even downloaded: the module is
 * imported on the first `apply(true)`. Saying no later opts the loaded client out and
 * deletes what it stored; agreeing again on the same page starts a new anonymous id.
 *
 * After a no, PostHog must not be contacted at all (BEAV-221). posthog.reset() is therefore
 * never called on a no: it reloads feature flags, a request carrying a fresh anonymous id.
 *
 * Nothing personal goes to PostHog: no identify call, so events stay anonymous; no
 * autocapture (it reads element text, which can hold names and addresses); no session
 * recording; page URLs lose their query string and fragment (sign-in and checkout put
 * codes and ids there).
 */

/** The part of the PostHog client used here. */
export interface PostHogClient {
  init(key: string, config: Record<string, unknown>): unknown;
  opt_in_capturing(): void;
  opt_out_capturing(): void;
  has_opted_out_capturing(): boolean;
  /** `true` also replaces the device id, which otherwise keeps the first anonymous id. */
  reset(resetDeviceId?: boolean): void;
}

export type PostHogLoader = () => Promise<PostHogClient>;

export const EU_HOST = 'https://eu.i.posthog.com';

type PostHogEvent = { properties?: Record<string, unknown> } | null;

/** Strips the query string and fragment from every URL property of an event. */
export function withoutQuery(event: PostHogEvent): PostHogEvent {
  if (!event?.properties) return event;
  const properties = { ...event.properties };
  for (const key of ['$current_url', '$referrer', '$initial_referrer', '$prev_pageview_url']) {
    const value = properties[key];
    if (typeof value === 'string') properties[key] = value.split(/[?#]/)[0];
  }
  return { ...event, properties };
}

export function postHogConfig(host: string): Record<string, unknown> {
  return {
    api_host: host,
    autocapture: false,
    capture_pageview: 'history_change',
    capture_pageleave: false,
    disable_session_recording: true,
    disable_surveys: true,
    // Each event leaves when it is captured, while the yes stands. Batched, it waited up to 3 s
    // (or for a sendBeacon on unload), and opting out doesn't empty that queue (BEAV-221).
    request_batching: false,
    // Off until opt_in_capturing(), also when its record of a no is gone: the cleanup deletes
    // __ph_opt_in_out_<key>, and PostHog would otherwise capture again on that page (BEAV-221).
    opt_out_capturing_by_default: true,
    // Opted out, PostHog keeps nothing in cookies or storage (reset() would write a new id).
    opt_out_persistence_by_default: true,
    person_profiles: 'identified_only',
    before_send: withoutQuery,
  };
}

export interface Analytics {
  /** Starts analytics when `granted` (and a project key is configured), stops it otherwise. */
  apply(granted: boolean): Promise<void>;
}

/** The parts of the browser PostHog stores in, so the cleanup can be tested without one. */
export interface BrowserStorage {
  hostname: string;
  cookieNames(): string[];
  /** Expires `name` as set for `domain`, or as a host-only cookie without one. */
  expireCookie(name: string, domain?: string): void;
  localStorageKeys(): string[];
  removeLocalStorage(key: string): void;
}

// posthog-js names its storage ph_<project key>_posthog and __ph_opt_in_out_<project key>.
const POSTHOG_NAME = /^_{0,2}ph_/;

/**
 * Deletes PostHog's cookies and localStorage keys. A cookie is only deleted with the domain
 * it was set with, and PostHog sets it on the parent domain to share it across subdomains,
 * so each name is expired host-only and on every parent of the hostname.
 */
export function clearPostHogStorage(browser: BrowserStorage): void {
  const labels = browser.hostname.split('.');
  const domains = labels.slice(0, -1).map((_, i) => labels.slice(i).join('.'));
  for (const name of browser.cookieNames().filter((cookie) => POSTHOG_NAME.test(cookie))) {
    browser.expireCookie(name);
    for (const domain of domains) browser.expireCookie(name, `.${domain}`);
  }
  for (const key of browser.localStorageKeys().filter((item) => POSTHOG_NAME.test(item))) {
    browser.removeLocalStorage(key);
  }
}

/** The real browser's storage; empty when cookies or storage are blocked. */
export function documentStorage(): BrowserStorage {
  return {
    hostname: window.location.hostname,
    cookieNames: () =>
      document.cookie
        .split(';')
        .map((part) => part.split('=')[0]?.trim() ?? '')
        .filter(Boolean),
    expireCookie: (name, domain) => {
      document.cookie = `${name}=; Path=/; Max-Age=0${domain ? `; Domain=${domain}` : ''}`;
    },
    localStorageKeys: () => {
      try {
        return Object.keys(window.localStorage);
      } catch {
        return [];
      }
    },
    removeLocalStorage: (key) => {
      try {
        window.localStorage.removeItem(key);
      } catch {
        // Storage blocked: nothing was stored there either.
      }
    },
  };
}

export function createAnalytics({
  key,
  host = EU_HOST,
  load,
  clearStorage,
}: {
  key: string | undefined;
  host?: string;
  load: PostHogLoader;
  /** Deletes PostHog's cookies and storage; runs on every no. */
  clearStorage: () => void;
}): Analytics {
  let client: Promise<PostHogClient> | null = null;
  let withdrawn = false;
  return {
    async apply(granted) {
      if (!granted) {
        if (client) {
          (await client).opt_out_capturing();
          withdrawn = true;
        }
        // Also when never loaded on this page: an earlier page may have stored an id.
        clearStorage();
        return;
      }
      if (!key) return;
      if (client) {
        const loaded = await client;
        // The ids from before the withdrawal stay in memory; don't link the two visits. reset()
        // clears PostHog's consent state, so it comes before opting in.
        if (withdrawn) loaded.reset(true);
        withdrawn = false;
        loaded.opt_in_capturing();
        return;
      }
      client = load().then((posthog) => {
        posthog.init(key, postHogConfig(host));
        // Off by default (postHogConfig), so this always opts in; it also captures the page view.
        if (posthog.has_opted_out_capturing()) posthog.opt_in_capturing();
        return posthog;
      });
      await client;
    },
  };
}

/** The app's analytics: the PostHog EU project from the environment; off without a key. */
export const analytics = createAnalytics({
  key: process.env.NEXT_PUBLIC_POSTHOG_KEY,
  host: process.env.NEXT_PUBLIC_POSTHOG_HOST || EU_HOST,
  load: () => import('posthog-js').then((module) => module.default as PostHogClient),
  clearStorage: () => clearPostHogStorage(documentStorage()),
});

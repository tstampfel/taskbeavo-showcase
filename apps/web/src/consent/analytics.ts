/**
 * PostHog analytics, only after consent (BEAV-83; plan, GDPR section "Correction, objection
 * and consent"). Until the visitor agrees, posthog-js isn't even downloaded: the module is
 * imported on the first `apply(true)`. Saying no later opts the loaded client out and
 * forgets its anonymous id.
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
  reset(): void;
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
    person_profiles: 'identified_only',
    before_send: withoutQuery,
  };
}

export interface Analytics {
  /** Starts analytics when `granted` (and a project key is configured), stops it otherwise. */
  apply(granted: boolean): Promise<void>;
}

export function createAnalytics({
  key,
  host = EU_HOST,
  load,
}: {
  key: string | undefined;
  host?: string;
  load: PostHogLoader;
}): Analytics {
  let client: Promise<PostHogClient> | null = null;
  return {
    async apply(granted) {
      if (!granted) {
        // Never loaded means nothing was ever captured: nothing to stop.
        if (!client) return;
        const loaded = await client;
        loaded.opt_out_capturing();
        loaded.reset();
        return;
      }
      if (!key) return;
      if (client) {
        (await client).opt_in_capturing();
        return;
      }
      client = load().then((posthog) => {
        posthog.init(key, postHogConfig(host));
        // An earlier withdrawal is remembered in the browser; agreeing again lifts it.
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
});

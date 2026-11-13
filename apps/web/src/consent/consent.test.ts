import { describe, expect, it, vi } from 'vitest';
import {
  createAnalytics,
  EU_HOST,
  postHogConfig,
  withoutQuery,
  type PostHogClient,
} from './analytics';
import { CONSENT_COOKIE, parseConsent, serializeConsent, visitorId } from './consent-cookie';

const VISITOR = '6f1c9a8e-2d4b-4c1e-9a77-0b5e3c2d1f00';

function fakePostHog() {
  const client: PostHogClient = {
    init: vi.fn(),
    opt_in_capturing: vi.fn(),
    opt_out_capturing: vi.fn(),
    has_opted_out_capturing: vi.fn(() => false),
    reset: vi.fn(),
  };
  const load = vi.fn(() => Promise.resolve(client));
  return { client, load };
}

describe('analytics before consent', () => {
  it('never loads PostHog without consent', async () => {
    const { load } = fakePostHog();
    const analytics = createAnalytics({ key: 'phc_test', load });

    await analytics.apply(false);
    await analytics.apply(false);

    expect(load).not.toHaveBeenCalled();
  });

  it('loads and starts PostHog EU only once the visitor agrees', async () => {
    const { client, load } = fakePostHog();
    const analytics = createAnalytics({ key: 'phc_test', load });

    await analytics.apply(true);
    await analytics.apply(true);

    expect(load).toHaveBeenCalledTimes(1);
    expect(client.init).toHaveBeenCalledWith('phc_test', postHogConfig(EU_HOST));
    expect(client.opt_in_capturing).toHaveBeenCalledTimes(1);
  });

  it('stays off without a project key, even with consent', async () => {
    const { load } = fakePostHog();
    await createAnalytics({ key: undefined, load }).apply(true);
    expect(load).not.toHaveBeenCalled();
  });

  it('opts out and forgets the anonymous id when consent is withdrawn', async () => {
    const { client, load } = fakePostHog();
    const analytics = createAnalytics({ key: 'phc_test', load });

    await analytics.apply(true);
    await analytics.apply(false);

    expect(client.opt_out_capturing).toHaveBeenCalledTimes(1);
    expect(client.reset).toHaveBeenCalledTimes(1);
  });

  it('sends no personal data: no autocapture, no recordings, no query strings', () => {
    const config = postHogConfig(EU_HOST);
    expect(config).toMatchObject({
      api_host: 'https://eu.i.posthog.com',
      autocapture: false,
      disable_session_recording: true,
      person_profiles: 'identified_only',
    });
    expect(
      withoutQuery({
        properties: {
          $current_url: 'https://taskbeavo.example/de/sign-in?email=a%40example.com#x',
          $referrer: 'https://taskbeavo.example/de/tasks/7?next=/x',
          event: 'kept',
        },
      }),
    ).toEqual({
      properties: {
        $current_url: 'https://taskbeavo.example/de/sign-in',
        $referrer: 'https://taskbeavo.example/de/tasks/7',
        event: 'kept',
      },
    });
  });
});

describe('the consent cookie', () => {
  it('round-trips the choice', () => {
    const choice = { analytics: true, visitorId: VISITOR, version: 'cookies-1' };
    const assignment = serializeConsent(choice, true);

    expect(assignment).toMatch(/; Path=\/; Max-Age=15552000; SameSite=Lax; Secure$/);
    expect(parseConsent(`other=1; ${assignment.split(';')[0]}`)).toEqual(choice);
  });

  it('treats a missing or tampered cookie as no answer, so analytics stays off', () => {
    expect(parseConsent('')).toBeNull();
    expect(parseConsent(`${CONSENT_COOKIE}=not-json`)).toBeNull();
    expect(
      parseConsent(`${CONSENT_COOKIE}=${encodeURIComponent('{"analytics":"yes"}')}`),
    ).toBeNull();
    expect(
      parseConsent(`${CONSENT_COOKIE}=${encodeURIComponent('{"analytics":true,"visitorId":"x"}')}`),
    ).toBeNull();
  });

  it('keeps the visitor id once chosen', () => {
    expect(visitorId({ analytics: false, visitorId: VISITOR, version: null })).toBe(VISITOR);
    expect(visitorId(null)).toMatch(/^[0-9a-f-]{36}$/);
  });
});

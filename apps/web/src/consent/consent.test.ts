import { describe, expect, it, vi } from 'vitest';
import {
  clearPostHogStorage,
  createAnalytics,
  EU_HOST,
  postHogConfig,
  withoutQuery,
  type BrowserStorage,
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
    const analytics = createAnalytics({ key: 'phc_test', load, clearStorage: vi.fn() });

    await analytics.apply(false);
    await analytics.apply(false);

    expect(load).not.toHaveBeenCalled();
  });

  it('loads and starts PostHog EU only once the visitor agrees', async () => {
    const { client, load } = fakePostHog();
    const analytics = createAnalytics({ key: 'phc_test', load, clearStorage: vi.fn() });

    await analytics.apply(true);
    await analytics.apply(true);

    expect(load).toHaveBeenCalledTimes(1);
    expect(client.init).toHaveBeenCalledWith('phc_test', postHogConfig(EU_HOST));
    expect(client.opt_in_capturing).toHaveBeenCalledTimes(1);
  });

  it('stays off without a project key, even with consent', async () => {
    const { load } = fakePostHog();
    await createAnalytics({ key: undefined, load, clearStorage: vi.fn() }).apply(true);
    expect(load).not.toHaveBeenCalled();
  });

  it('opts out on withdrawal without reset(), which would contact PostHog again', async () => {
    // reset() reloads feature flags: a request with a fresh anonymous id after the no (BEAV-221).
    const { client, load } = fakePostHog();
    const analytics = createAnalytics({ key: 'phc_test', load, clearStorage: vi.fn() });

    await analytics.apply(true);
    await analytics.apply(false);

    expect(client.opt_out_capturing).toHaveBeenCalledTimes(1);
    expect(client.reset).not.toHaveBeenCalled();
  });

  it('starts a new anonymous id when the visitor agrees again on the same page', async () => {
    const { client, load } = fakePostHog();
    const analytics = createAnalytics({ key: 'phc_test', load, clearStorage: vi.fn() });

    await analytics.apply(true);
    await analytics.apply(false);
    await analytics.apply(true);

    // reset(true): a new device id too, or the old id rides along as $device_id.
    expect(client.reset).toHaveBeenCalledTimes(1);
    expect(client.reset).toHaveBeenCalledWith(true);
    const resetAt = vi.mocked(client.reset).mock.invocationCallOrder[0] ?? 0;
    const optedInAt = vi.mocked(client.opt_in_capturing).mock.invocationCallOrder.at(-1) ?? 0;
    // reset() clears PostHog's consent state, so it must come before opting in, not after.
    expect(resetAt).toBeLessThan(optedInAt);
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

/**
 * A browser's cookie jar and localStorage, enough to see what PostHog leaves behind. A cookie
 * is deleted only by an expiring assignment with the same domain it was set with.
 */
function fakeBrowser(hostname: string, cookies: Record<string, string | undefined>) {
  // name -> domain it was set with ('' = host-only)
  const jar = new Map(Object.entries(cookies).map(([name, domain]) => [name, domain ?? '']));
  const local = new Map<string, string>([
    ['ph_phc_test_posthog', '{"distinct_id":"anon-1"}'],
    ['__ph_opt_in_out_phc_test', '0'],
    ['taskbeavo.termsUpdateShownTo', 'u1:v1'],
  ]);
  const browser: BrowserStorage = {
    hostname,
    cookieNames: () => [...jar.keys()],
    expireCookie: (name, domain) => {
      if (jar.get(name) === (domain ?? '')) jar.delete(name);
    },
    localStorageKeys: () => [...local.keys()],
    removeLocalStorage: (key) => void local.delete(key),
  };
  return { browser, jar, local };
}

describe('withdrawing analytics', () => {
  it('sends each event at once, so none is left queued to leave after a no', () => {
    expect(postHogConfig(EU_HOST)).toMatchObject({ request_batching: false });
  });

  it('keeps PostHog off once its own record of the no is deleted with the rest', () => {
    // clearPostHogStorage removes __ph_opt_in_out_<key>; without a record PostHog would
    // otherwise go back to capturing on the same page.
    expect(postHogConfig(EU_HOST)).toMatchObject({ opt_out_capturing_by_default: true });
  });

  it('turns off PostHog persistence when opted out', () => {
    expect(postHogConfig(EU_HOST)).toMatchObject({ opt_out_persistence_by_default: true });
  });

  it('stops PostHog, then removes its cookies and storage', async () => {
    const { client, load } = fakePostHog();
    const clear = vi.fn();
    const analytics = createAnalytics({ key: 'phc_test', load, clearStorage: clear });

    await analytics.apply(true);
    expect(clear).not.toHaveBeenCalled();
    await analytics.apply(false);

    expect(client.opt_out_capturing).toHaveBeenCalledTimes(1);
    expect(clear).toHaveBeenCalledTimes(1);
  });

  it('removes what an earlier visit left, without loading PostHog', async () => {
    const { load } = fakePostHog();
    const clear = vi.fn();

    await createAnalytics({ key: 'phc_test', load, clearStorage: clear }).apply(false);

    expect(load).not.toHaveBeenCalled();
    expect(clear).toHaveBeenCalledTimes(1);
  });

  it('deletes ph_ cookies set on the host or a parent domain, and ph_ storage keys', () => {
    const { browser, jar, local } = fakeBrowser('www.taskbeavo.example', {
      ph_phc_test_posthog: '.taskbeavo.example',
      __ph_opt_in_out_phc_test: undefined,
      tb_consent: undefined,
      NEXT_LOCALE: undefined,
    });

    clearPostHogStorage(browser);

    expect([...jar.keys()].sort()).toEqual(['NEXT_LOCALE', 'tb_consent']);
    expect([...local.keys()]).toEqual(['taskbeavo.termsUpdateShownTo']);
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

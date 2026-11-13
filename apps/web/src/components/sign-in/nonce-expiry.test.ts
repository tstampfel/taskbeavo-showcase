import { describe, expect, it, vi } from 'vitest';
import {
  NONCE_MIN_REFRESH_MS,
  NONCE_REFRESH_MARGIN_MS,
  NONCE_TTL_MS,
  nonceRefreshDelay,
  watchNonceExpiry,
  type ExpiryEnv,
} from './nonce-expiry';

function fakeEnv(start = 0) {
  let now = start;
  const timers: { at: number; run: () => void }[] = [];
  const listeners = new Set<() => void>();
  const doc = { visibilityState: 'visible' as DocumentVisibilityState };
  const env: ExpiryEnv = {
    now: () => now,
    setTimeout: (run, ms) => {
      const timer = { at: now + ms, run };
      timers.push(timer);
      return timer;
    },
    clearTimeout: (handle) => {
      const i = timers.indexOf(handle as (typeof timers)[number]);
      if (i >= 0) timers.splice(i, 1);
    },
    document: {
      get visibilityState() {
        return doc.visibilityState;
      },
      addEventListener: (_type: string, listener: () => void) => void listeners.add(listener),
      removeEventListener: (_type: string, listener: () => void) => void listeners.delete(listener),
    } as unknown as ExpiryEnv['document'],
  };
  return {
    env,
    doc,
    listeners,
    /** Moves the clock; `fireTimers: false` is a background tab whose timers were held back. */
    advance(ms: number, fireTimers = true) {
      now += ms;
      if (!fireTimers) return;
      for (const timer of [...timers]) if (timer.at <= now) timer.run();
    },
    showTab() {
      doc.visibilityState = 'visible';
      for (const listener of [...listeners]) listener();
    },
  };
}

const NINE_MINUTES = 9 * 60_000;

describe('nonceRefreshDelay', () => {
  it('is the API TTL minus a minute', () => {
    expect(nonceRefreshDelay()).toBe(NONCE_TTL_MS - NONCE_REFRESH_MARGIN_MS);
    expect(nonceRefreshDelay()).toBe(NINE_MINUTES);
  });

  it('never drops below the minimum, so it cannot loop into the rate limit', () => {
    expect(nonceRefreshDelay(30_000)).toBe(NONCE_MIN_REFRESH_MS);
    expect(nonceRefreshDelay(0)).toBe(NONCE_MIN_REFRESH_MS);
    expect(nonceRefreshDelay(Number.NaN)).toBe(NONCE_MIN_REFRESH_MS);
  });
});

describe('watchNonceExpiry', () => {
  // The server issued the nonce at 09:00 with expiresAt 09:10; the device clock may disagree.
  const serverIssuedAt = Date.parse('2030-01-01T09:00:00Z');

  it.each([
    [
      'a device clock 15 minutes fast (expiresAt already in the past)',
      serverIssuedAt + 15 * 60_000,
    ],
    ['a device clock 15 minutes slow', serverIssuedAt - 15 * 60_000],
    ['a correct device clock', serverIssuedAt],
  ])('waits nine minutes from receipt with %s', (_name, deviceNow) => {
    const clock = fakeEnv(deviceNow);
    const onDue = vi.fn();
    watchNonceExpiry(onDue, clock.env);

    clock.advance(0);
    clock.showTab();
    expect(onDue).not.toHaveBeenCalled();
    clock.advance(NINE_MINUTES - 1);
    expect(onDue).not.toHaveBeenCalled();
    clock.advance(1);
    expect(onDue).toHaveBeenCalledTimes(1);
  });

  it('does not need expiresAt at all, so a missing or unreadable one changes nothing', () => {
    // The watcher takes no server time: it runs on the default delay from when it starts.
    const clock = fakeEnv(1_000);
    const onDue = vi.fn();
    watchNonceExpiry(onDue, clock.env);
    clock.advance(NONCE_MIN_REFRESH_MS - 1);
    clock.showTab();
    expect(onDue).not.toHaveBeenCalled();
    clock.advance(NINE_MINUTES);
    expect(onDue).toHaveBeenCalledTimes(1);
  });

  it('replaces the nonce before it expires on an open page', () => {
    const clock = fakeEnv();
    const onDue = vi.fn();
    watchNonceExpiry(onDue, clock.env);

    clock.advance(8 * 60_000);
    expect(onDue).not.toHaveBeenCalled();
    clock.advance(60_000);
    expect(onDue).toHaveBeenCalledTimes(1);
    clock.advance(60 * 60_000);
    expect(onDue).toHaveBeenCalledTimes(1);
  });

  it('catches up when a background tab comes back after its timer was held', () => {
    const clock = fakeEnv();
    const onDue = vi.fn();
    watchNonceExpiry(onDue, clock.env);

    clock.doc.visibilityState = 'hidden';
    clock.advance(30 * 60_000, false);
    expect(onDue).not.toHaveBeenCalled();
    clock.showTab();
    expect(onDue).toHaveBeenCalledTimes(1);
    expect(clock.listeners.size).toBe(0);
  });

  it('does nothing once cleaned up', () => {
    const clock = fakeEnv();
    const onDue = vi.fn();
    const stop = watchNonceExpiry(onDue, clock.env, 60_000);
    stop();
    clock.advance(120_000);
    clock.showTab();
    expect(onDue).not.toHaveBeenCalled();
  });
});

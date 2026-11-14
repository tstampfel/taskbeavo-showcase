import { describe, expect, it } from 'vitest';
import { fitsPinned, MAX_PINNED_SHARE } from './pinned-foot';

describe('fitsPinned', () => {
  it('pins the bar at the default text size on a phone', () => {
    // Measured at 375×667 and 375×812 with 100% text: 153–191px.
    expect(fitsPinned(191, 667)).toBe(true);
    expect(fitsPinned(191, 812)).toBe(true);
  });

  it('leaves it in the page with 200% text, where it would cover the consent boxes', () => {
    // Measured with 200% text (review R1-1): 355px in en, up to 439px in fr.
    expect(fitsPinned(355, 812)).toBe(false);
    expect(fitsPinned(439, 812)).toBe(false);
    expect(fitsPinned(355, 667)).toBe(false);
  });

  it('never pins before the screen is measured', () => {
    expect(fitsPinned(0, 0)).toBe(false);
  });

  it('takes at most a third of the screen and a bit', () => {
    expect(MAX_PINNED_SHARE).toBeLessThanOrEqual(0.35);
  });
});

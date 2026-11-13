import { describe, expect, it } from 'vitest';
import { markTermsUpdateOpening, termsUpdateOpening, termsUpdateShown } from './shown';

describe('the screen the banner is opening', () => {
  it('is remembered per account and version, without marking the screen shown', () => {
    expect(termsUpdateOpening('u1', '2026-12')).toBe(false);

    markTermsUpdateOpening('u1', '2026-12');

    expect(termsUpdateOpening('u1', '2026-12')).toBe(true);
    // A newer version or another account opens it again.
    expect(termsUpdateOpening('u1', '2027-01')).toBe(false);
    expect(termsUpdateOpening('u2', '2026-12')).toBe(false);
    // Shown stays for the screen itself to record.
    expect(termsUpdateShown('u1', '2026-12')).toBe(false);
  });
});

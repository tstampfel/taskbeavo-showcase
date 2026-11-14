// The account and terms version "We've updated our terms" was last shown for in this tab, so
// it opens by itself once per tab, account and version: another account signing in here, or a
// newer version taking effect meanwhile, gets it again.
const SHOWN_KEY = 'taskbeavo.termsUpdateShownTo';

function shownValue(viewerId: string, version: string | undefined): string {
  return `${viewerId}:${version ?? ''}`;
}

export function termsUpdateShown(viewerId: string, version: string | undefined): boolean {
  try {
    return window.sessionStorage.getItem(SHOWN_KEY) === shownValue(viewerId, version);
  } catch {
    // Storage blocked: the banner still offers the page.
    return false;
  }
}

/** Called by the page itself too, so a visit that starts there doesn't open it again. */
export function markTermsUpdateShown(viewerId: string, version: string | undefined): void {
  try {
    window.sessionStorage.setItem(SHOWN_KEY, shownValue(viewerId, version));
  } catch {
    // Storage blocked: at worst the page opens once more.
  }
}

// The screen this document is on its way to open, in memory only. The banner remembers it
// instead of marking the screen shown: a push the next full page load cuts short never lands,
// and a mark written before it would keep the screen from opening there. The page marks it
// shown once it has opened.
let opening: string | null = null;

/** The banner set off to open the screen for this account and version, in this document. */
export function termsUpdateOpening(viewerId: string, version: string | undefined): boolean {
  return opening === shownValue(viewerId, version);
}

export function markTermsUpdateOpening(viewerId: string, version: string | undefined): void {
  opening = shownValue(viewerId, version);
}

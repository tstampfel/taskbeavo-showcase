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

import { appPathFromLink } from '../src/links/routes';

/**
 * Every incoming URL (universal link, App Link, `taskbeavo://` link) passes through here before
 * Expo Router navigates. Pushes don't yet: the tap handler that comes with push registration
 * must run `data.route` through `appPathFromLink` too, including the response that launched
 * the app (`getLastNotificationResponseAsync`).
 *
 * Known links open their screen; anything else opens the home screen on launch and is ignored
 * while the app is running, so a stray link never moves the user somewhere unexpected.
 */
export function redirectSystemPath({ path, initial }: { path: string; initial: boolean }) {
  // appPathFromLink doesn't throw today; Expo asks for a guard because a throw here crashes the
  // app before any error boundary exists.
  try {
    const target = appPathFromLink(path);
    return target === '/' && !initial ? null : target;
  } catch {
    return initial ? '/' : null;
  }
}

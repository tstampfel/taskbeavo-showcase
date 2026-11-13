import { APP_SCHEME, isAppRoute, LINK_HOSTS, LINK_LOCALES } from '@taskbeavo/app-links';

const HOME = '/';

/** The path part of a link, or null when it isn't one of ours. */
function linkPath(link: string): string | null {
  if (link.startsWith('/')) return link;
  let url: URL;
  try {
    url = new URL(link);
  } catch {
    return null;
  }
  if (url.protocol === 'https:') {
    return (LINK_HOSTS as readonly string[]).includes(url.hostname) && url.port === ''
      ? url.pathname
      : null;
  }
  if (url.protocol === `${APP_SCHEME}:`) {
    // `taskbeavo://bookings/7` parses "bookings" as the host; `taskbeavo:///bookings/7` doesn't.
    const rest = link.slice(`${APP_SCHEME}:`.length).replace(/^\/+/, '');
    return `/${rest}`;
  }
  return null;
}

/**
 * Maps an incoming link (universal link, App Link or `taskbeavo://` URL) to the app path it
 * opens. It also takes a bare path, so the push tap handler, once built, can pass a push's
 * `data.route` through it. The locale prefix of web URLs is dropped (the app keeps its own
 * language setting), and the query string and fragment are dropped so a link can never carry
 * an instruction. Anything unknown opens the home screen.
 */
export function appPathFromLink(link: string): string {
  const raw = linkPath(link);
  if (raw === null) return HOME;
  let path = raw.split(/[?#]/, 1)[0] ?? '';
  const [, first] = path.split('/');
  if (first !== undefined && (LINK_LOCALES as readonly string[]).includes(first)) {
    path = path.slice(first.length + 1);
  }
  if (path.length > 1 && path.endsWith('/')) path = path.slice(0, -1);
  return isAppRoute(path) ? path : HOME;
}

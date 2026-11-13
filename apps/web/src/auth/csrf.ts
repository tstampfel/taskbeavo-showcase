/**
 * Cross-site request forgery check for the /api route handlers.
 *
 * The session cookies ride along on every request to this origin, and Django's GraphQL
 * endpoint is csrf_exempt because it expects bearer tokens, so the handlers that turn a
 * cookie into a bearer token must refuse requests another site's page started. A request
 * passes only when it is a JSON POST carrying CSRF_HEADER (a custom header a cross-site
 * page can't send without a CORS preflight, which this origin never grants), its Origin
 * names this host, and Sec-Fetch-Site, where the browser sends it, says same-origin.
 */

export const CSRF_HEADER = 'x-taskbeavo-csrf';
export const CSRF_HEADER_VALUE = '1';

/** The headers every same-origin call to the /api route handlers sends. */
export const SAME_ORIGIN_HEADERS = {
  'content-type': 'application/json',
  [CSRF_HEADER]: CSRF_HEADER_VALUE,
} as const;

export function isSameOriginRequest(request: { method: string; headers: Headers }): boolean {
  const { headers } = request;
  if (request.method !== 'POST') return false;
  if (!(headers.get('content-type') ?? '').toLowerCase().startsWith('application/json')) {
    return false;
  }
  if (headers.get(CSRF_HEADER) !== CSRF_HEADER_VALUE) return false;
  const fetchSite = headers.get('sec-fetch-site');
  if (fetchSite !== null && fetchSite !== 'same-origin') return false;
  // Browsers send Origin on every POST, so a missing one isn't a browser page of ours.
  const origin = headers.get('origin');
  const host = headers.get('x-forwarded-host') ?? headers.get('host');
  if (!origin || !host) return false;
  try {
    return new URL(origin).host === host.split(',')[0]?.trim();
  } catch {
    return false;
  }
}

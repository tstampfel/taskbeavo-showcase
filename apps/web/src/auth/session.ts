import type { AuthSessionFieldsFragment } from '@taskbeavo/graphql';
import { RefreshSessionDocument } from '@taskbeavo/graphql';
import { callApi, errorCode, upstreamHeaders } from './upstream';

/**
 * The web session lives in two httpOnly cookies that page code can't read: the access
 * token (sent to Django as a bearer token by the /api route handlers) and the refresh
 * token. The access cookie expires a little before its token, so a request without it
 * refreshes first instead of reaching Django with a dead token.
 */
export const ACCESS_COOKIE = 'tb_access';
export const REFRESH_COOKIE = 'tb_refresh';
/**
 * The device token the API hands out at sign-in (AuthSession.deviceToken). It outlives
 * sign-out, so the next sign-in from this browser isn't reported as a new device; that
 * is its only use. A strictly necessary security cookie (decision 2026-11-04, ePrivacy
 * Art. 5(3) exemption, WP194 "user-centric security"), so it lasts 6 months, renewed at
 * each sign-in; the API forgets a token unused that long too (DEVICE_TOKEN_MAX_AGE_DAYS).
 */
export const DEVICE_COOKIE = 'tb_device';

export const DEVICE_COOKIE_MAX_AGE_SECONDS = 183 * 24 * 60 * 60;

const ACCESS_COOKIE_EARLY_EXPIRY_MS = 30_000;

export interface CookieChange {
  name: string;
  value: string;
  options: {
    httpOnly: true;
    secure: boolean;
    sameSite: 'lax';
    path: '/';
    expires: Date;
    maxAge?: number;
  };
}

interface CookieReader {
  get(name: string): { value: string } | undefined;
}

/** Any cookie a route handler writes: a session cookie, or the locale cookie (i18n/locale-cookie.ts). */
export interface CookieWrite {
  name: string;
  value: string;
  options: {
    httpOnly?: boolean;
    secure?: boolean;
    sameSite: 'lax';
    path: '/';
    expires?: Date;
    maxAge?: number;
  };
}

interface CookieWriter {
  set(name: string, value: string, options: CookieWrite['options']): unknown;
}

/** Plain http only on a developer's machine; everywhere else the cookies are Secure. */
function secureCookies(env: Record<string, string | undefined>): boolean {
  return env.NODE_ENV === 'production';
}

function cookie(
  name: string,
  value: string,
  expires: Date,
  env: Record<string, string | undefined>,
): CookieChange {
  return {
    name,
    value,
    options: { httpOnly: true, secure: secureCookies(env), sameSite: 'lax', path: '/', expires },
  };
}

function deviceCookie(token: string, env: Record<string, string | undefined>): CookieChange {
  const change = cookie(
    DEVICE_COOKIE,
    token,
    new Date(Date.now() + DEVICE_COOKIE_MAX_AGE_SECONDS * 1000),
    env,
  );
  return { ...change, options: { ...change.options, maxAge: DEVICE_COOKIE_MAX_AGE_SECONDS } };
}

/** The cookies that store a freshly issued session, and its device token when it has one. */
export function sessionCookies(
  session: Pick<
    AuthSessionFieldsFragment,
    'accessToken' | 'accessTokenExpiresAt' | 'refreshToken' | 'refreshTokenExpiresAt'
  > &
    Partial<Pick<AuthSessionFieldsFragment, 'deviceToken'>>,
  env: Record<string, string | undefined> = process.env,
): CookieChange[] {
  const accessExpires = new Date(
    new Date(String(session.accessTokenExpiresAt)).getTime() - ACCESS_COOKIE_EARLY_EXPIRY_MS,
  );
  const device = session.deviceToken ? [deviceCookie(session.deviceToken, env)] : [];
  return [
    ...device,
    cookie(ACCESS_COOKIE, session.accessToken, accessExpires, env),
    cookie(
      REFRESH_COOKIE,
      session.refreshToken,
      new Date(String(session.refreshTokenExpiresAt)),
      env,
    ),
  ];
}

/** Cookies that delete the session (signed out or the session ended). */
export function clearedSessionCookies(
  env: Record<string, string | undefined> = process.env,
): CookieChange[] {
  return [ACCESS_COOKIE, REFRESH_COOKIE].map((name) => cookie(name, '', new Date(0), env));
}

export function applyCookies(target: { cookies: CookieWriter }, changes: CookieWrite[]): void {
  for (const change of changes) target.cookies.set(change.name, change.value, change.options);
}

/**
 * The access token to send for `request`, refreshing the session when only the refresh
 * cookie is left, plus the cookie changes the response must carry. A refresh the API
 * refuses ends the session (cookies cleared); one that can't reach the API leaves the
 * cookies alone and goes on signed out.
 */
export async function accessTokenFor(request: {
  headers: Headers;
  cookies: CookieReader;
}): Promise<{ accessToken?: string; cookies: CookieChange[] }> {
  const access = request.cookies.get(ACCESS_COOKIE)?.value;
  if (access) return { accessToken: access, cookies: [] };
  const refreshToken = request.cookies.get(REFRESH_COOKIE)?.value;
  if (!refreshToken) return { cookies: [] };
  let body;
  try {
    body = await callApi(
      RefreshSessionDocument,
      { refreshToken },
      upstreamHeaders(request, undefined),
    );
  } catch {
    return { cookies: [] };
  }
  const session = body.data?.refreshSession;
  if (session) return { accessToken: session.accessToken, cookies: sessionCookies(session) };
  if (errorCode(body) === 'SESSION_EXPIRED') return { cookies: clearedSessionCookies() };
  return { cookies: [] };
}

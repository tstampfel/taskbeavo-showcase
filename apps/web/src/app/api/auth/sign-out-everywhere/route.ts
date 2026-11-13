import { SignOutEverywhereDocument } from '@taskbeavo/graphql';
import { NextResponse, type NextRequest } from 'next/server';
import { isSameOriginRequest } from '../../../../auth/csrf';
import { apiUnavailable, errorsOnly, rejected } from '../../../../auth/handlers';
import { accessTokenFor, applyCookies, clearedSessionCookies } from '../../../../auth/session';
import { callApi, errorCode, upstreamHeaders } from '../../../../auth/upstream';

/**
 * Log out of all devices: the API revokes every session of the account and closes their
 * WebSockets, and this browser's cookies go. Unlike signing out here, the page must hear
 * when it didn't work, so an unreachable API answers 502 and leaves the cookies.
 */
export async function POST(request: NextRequest): Promise<NextResponse> {
  if (!isSameOriginRequest(request)) return rejected('Cross-site request refused.');
  const { accessToken, cookies } = await accessTokenFor(request);
  let body;
  try {
    body = await callApi(SignOutEverywhereDocument, {}, upstreamHeaders(request, accessToken));
  } catch {
    const response = apiUnavailable();
    applyCookies(response, cookies);
    return response;
  }
  if (body.data?.signOutEverywhere) {
    const response = NextResponse.json({ data: { signOutEverywhere: true } });
    applyCookies(response, clearedSessionCookies());
    return response;
  }
  const response = errorsOnly(body);
  // Already signed out (the session ended elsewhere): nothing left to keep.
  applyCookies(response, errorCode(body) === 'UNAUTHENTICATED' ? clearedSessionCookies() : cookies);
  return response;
}

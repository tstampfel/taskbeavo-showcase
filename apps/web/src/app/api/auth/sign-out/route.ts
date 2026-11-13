import { SignOutDocument } from '@taskbeavo/graphql';
import { NextResponse, type NextRequest } from 'next/server';
import { isSameOriginRequest } from '../../../../auth/csrf';
import { rejected } from '../../../../auth/handlers';
import { applyCookies, clearedSessionCookies, REFRESH_COOKIE } from '../../../../auth/session';
import { callApi, upstreamHeaders } from '../../../../auth/upstream';

/**
 * Signs this browser out: revokes the session in the API and deletes the cookies. The
 * cookies go even when the API can't be reached, so the browser is signed out regardless.
 */
export async function POST(request: NextRequest): Promise<NextResponse> {
  if (!isSameOriginRequest(request)) return rejected('Cross-site request refused.');
  const refreshToken = request.cookies.get(REFRESH_COOKIE)?.value;
  if (refreshToken) {
    try {
      await callApi(SignOutDocument, { refreshToken }, upstreamHeaders(request, undefined));
    } catch {
      // Signed out locally; the session expires on its own.
    }
  }
  const response = NextResponse.json({ data: { signOut: true } });
  applyCookies(response, clearedSessionCookies());
  return response;
}

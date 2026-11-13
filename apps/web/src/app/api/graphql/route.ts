import { NextResponse, type NextRequest } from 'next/server';
import { serverGraphqlUrl } from '../../../apollo/endpoint';
import { isSameOriginRequest } from '../../../auth/csrf';
import { apiUnavailable, jsonObject, rejected } from '../../../auth/handlers';
import { accessTokenFor, applyCookies } from '../../../auth/session';
import { selectsTokenField } from '../../../auth/token-operations';
import { upstreamHeaders } from '../../../auth/upstream';

/**
 * The browser's GraphQL endpoint. It forwards the request to Django with the visitor's
 * access token from the httpOnly cookie as a bearer token (refreshing it first when only
 * the refresh cookie is left) and the visitor's address for the rate limit. Only POST:
 * a GET couldn't carry the CSRF header.
 */
export async function POST(request: NextRequest): Promise<NextResponse> {
  if (!isSameOriginRequest(request)) return rejected('Cross-site request refused.');
  const body = await jsonObject(request);
  if (body === null) return rejected('Send one GraphQL operation as a JSON object.');
  if (typeof body.query === 'string' && selectsTokenField(body.query)) {
    return rejected('Sign-in operations go through /api/auth.');
  }

  const { accessToken, cookies } = await accessTokenFor(request);
  let upstream: Response;
  try {
    upstream = await fetch(serverGraphqlUrl(), {
      method: 'POST',
      headers: upstreamHeaders(request, accessToken),
      body: JSON.stringify(body),
      cache: 'no-store',
    });
  } catch {
    return apiUnavailable();
  }
  const response = new NextResponse(upstream.body, {
    status: upstream.status,
    headers: { 'content-type': upstream.headers.get('content-type') ?? 'application/json' },
  });
  applyCookies(response, cookies);
  return response;
}

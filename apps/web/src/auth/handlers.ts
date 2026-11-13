import type { AuthSessionFieldsFragment } from '@taskbeavo/graphql';
import { NextResponse } from 'next/server';
import { DEVICE_COOKIE } from './session';
import type { GraphQLResponseBody } from './upstream';

/** 403 with a GraphQL-shaped error, so the page translates `errors.REQUEST_REJECTED`. */
export function rejected(message: string): NextResponse {
  return NextResponse.json(
    { errors: [{ message, extensions: { code: 'REQUEST_REJECTED' } }] },
    { status: 403 },
  );
}

/** 502 when Django can't be reached; Apollo surfaces it as a network error. */
export function apiUnavailable(): NextResponse {
  return NextResponse.json({ errors: [{ message: 'The API is unavailable.' }] }, { status: 502 });
}

/** The request's JSON object body, or null. */
export async function jsonObject(request: Request): Promise<Record<string, unknown> | null> {
  try {
    const body: unknown = await request.json();
    return typeof body === 'object' && body !== null && !Array.isArray(body)
      ? (body as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

/** A session as the page sees it: who signed in, without the tokens. */
export function withoutTokens(session: AuthSessionFieldsFragment) {
  return { __typename: 'AuthSession' as const, id: session.id, user: session.user };
}

/** Passes Django's errors through unchanged, without data. */
export function errorsOnly(body: GraphQLResponseBody<unknown>): NextResponse {
  return NextResponse.json({ data: null, errors: body.errors ?? [] });
}

/**
 * The DeviceInput of a sign-in from this browser: the page's `deviceName` and the device
 * token from the httpOnly cookie, which the page can't read.
 */
export function webDevice(
  request: { headers: Headers },
  body: Record<string, unknown>,
): { platform: 'WEB'; name: string; deviceToken?: string } {
  const deviceToken = cookieValue(request.headers, DEVICE_COOKIE);
  return { platform: 'WEB', name: deviceName(body), ...(deviceToken ? { deviceToken } : {}) };
}

function cookieValue(headers: Headers, name: string): string | undefined {
  for (const part of (headers.get('cookie') ?? '').split(';')) {
    const [key, ...value] = part.trim().split('=');
    if (key === name) return decodeURIComponent(value.join('='));
  }
  return undefined;
}

/** The optional `deviceName` the page sends, e.g. "Firefox on Mac". */
export function deviceName(body: Record<string, unknown>): string {
  return typeof body.deviceName === 'string' ? body.deviceName.slice(0, 100) : '';
}

import { VerifyEmailCodeDocument } from '@taskbeavo/graphql';
import { NextResponse } from 'next/server';
import { isSameOriginRequest } from '../../../../auth/csrf';
import {
  apiUnavailable,
  webDevice,
  errorsOnly,
  jsonObject,
  rejected,
  withoutTokens,
} from '../../../../auth/handlers';
import { applyCookies, sessionCookies } from '../../../../auth/session';
import { callApi, upstreamHeaders } from '../../../../auth/upstream';
import { accountLocaleCookie } from '../../../../i18n/locale-cookie';

/**
 * `verifyEmailCode` for the web app. Body: `{ email, code, deviceName? }`. An existing
 * account's tokens go into httpOnly cookies and the page gets `{ __typename:
 * "AuthSession", id, user }`; a new address gets AccountDetailsRequired as the API sends it.
 */
export async function POST(request: Request): Promise<NextResponse> {
  if (!isSameOriginRequest(request)) return rejected('Cross-site request refused.');
  const body = await jsonObject(request);
  if (body === null || typeof body.email !== 'string' || typeof body.code !== 'string') {
    return rejected('Send { email, code }.');
  }
  let result;
  try {
    result = await callApi(
      VerifyEmailCodeDocument,
      { email: body.email, code: body.code, device: webDevice(request, body) },
      upstreamHeaders(request, undefined),
    );
  } catch {
    return apiUnavailable();
  }
  const verified = result.data?.verifyEmailCode;
  if (!verified) return errorsOnly(result);
  if (verified.__typename === 'AccountDetailsRequired') {
    return NextResponse.json({ data: { verifyEmailCode: verified } });
  }
  const response = NextResponse.json({ data: { verifyEmailCode: withoutTokens(verified) } });
  // The account's saved language follows the user to this browser.
  applyCookies(response, [...sessionCookies(verified), accountLocaleCookie(verified.user.locale)]);
  return response;
}

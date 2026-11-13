import { LinkProviderWithEmailCodeDocument } from '@taskbeavo/graphql';
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
 * `linkProviderWithEmailCode` for the web app. Body: `{ ticket, code, deviceName? }`, the
 * ticket from ProviderLinkRequired and the code emailed to the account. The session's
 * tokens go into httpOnly cookies and the page gets `{ __typename: "AuthSession", id, user }`.
 */
export async function POST(request: Request): Promise<NextResponse> {
  if (!isSameOriginRequest(request)) return rejected('Cross-site request refused.');
  const body = await jsonObject(request);
  if (body === null || typeof body.ticket !== 'string' || typeof body.code !== 'string') {
    return rejected('Send { ticket, code }.');
  }
  let result;
  try {
    result = await callApi(
      LinkProviderWithEmailCodeDocument,
      { ticket: body.ticket, code: body.code, device: webDevice(request, body) },
      upstreamHeaders(request, undefined),
    );
  } catch {
    return apiUnavailable();
  }
  const session = result.data?.linkProviderWithEmailCode;
  if (!session) return errorsOnly(result);
  const response = NextResponse.json({
    data: { linkProviderWithEmailCode: withoutTokens(session) },
  });
  // The account's saved language follows the user to this browser.
  applyCookies(response, [...sessionCookies(session), accountLocaleCookie(session.user.locale)]);
  return response;
}

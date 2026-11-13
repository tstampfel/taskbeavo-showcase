import { CompleteAccountDetailsDocument, type AccountDetailsInput } from '@taskbeavo/graphql';
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
 * `completeAccountDetails` for the web app. Body: `{ details: AccountDetailsInput,
 * deviceName? }`; the API validates the details. The new session's tokens go into
 * httpOnly cookies and the page gets `{ __typename: "AuthSession", id, user }`.
 */
export async function POST(request: Request): Promise<NextResponse> {
  if (!isSameOriginRequest(request)) return rejected('Cross-site request refused.');
  const body = await jsonObject(request);
  if (body === null || typeof body.details !== 'object' || body.details === null) {
    return rejected('Send { details }.');
  }
  let result;
  try {
    result = await callApi(
      CompleteAccountDetailsDocument,
      {
        details: body.details as AccountDetailsInput,
        device: webDevice(request, body),
      },
      upstreamHeaders(request, undefined),
    );
  } catch {
    return apiUnavailable();
  }
  const session = result.data?.completeAccountDetails;
  if (!session) return errorsOnly(result);
  const response = NextResponse.json({ data: { completeAccountDetails: withoutTokens(session) } });
  // The account's saved language follows the user to this browser.
  applyCookies(response, [...sessionCookies(session), accountLocaleCookie(session.user.locale)]);
  return response;
}

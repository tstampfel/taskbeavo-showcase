import { SignInWithProviderDocument, type SignInProvider } from '@taskbeavo/graphql';
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

const PROVIDERS: ReadonlySet<string> = new Set<SignInProvider>(['GOOGLE', 'APPLE']);

/**
 * `signInWithProvider` for the web app. Body: `{ provider: "GOOGLE" | "APPLE", idToken,
 * nonce, firstName?, lastName?, deviceName? }`, with the nonce from `requestSignInNonce`.
 * Like verify-email-code: a known provider account's tokens go into httpOnly cookies and the
 * page gets `{ __typename: "AuthSession", id, user }`. AccountDetailsRequired (new account)
 * and ProviderLinkRequired (finish with /api/auth/link-provider) pass through as sent.
 */
export async function POST(request: Request): Promise<NextResponse> {
  if (!isSameOriginRequest(request)) return rejected('Cross-site request refused.');
  const body = await jsonObject(request);
  if (
    body === null ||
    typeof body.provider !== 'string' ||
    !PROVIDERS.has(body.provider) ||
    typeof body.idToken !== 'string' ||
    typeof body.nonce !== 'string'
  ) {
    return rejected('Send { provider, idToken, nonce }.');
  }
  const input = {
    provider: body.provider as SignInProvider,
    idToken: body.idToken,
    nonce: body.nonce,
    firstName: typeof body.firstName === 'string' ? body.firstName : '',
    lastName: typeof body.lastName === 'string' ? body.lastName : '',
  };
  let result;
  try {
    result = await callApi(
      SignInWithProviderDocument,
      { input, device: webDevice(request, body) },
      upstreamHeaders(request, undefined),
    );
  } catch {
    return apiUnavailable();
  }
  const signedIn = result.data?.signInWithProvider;
  if (!signedIn) return errorsOnly(result);
  if (signedIn.__typename !== 'AuthSession') {
    // AccountDetailsRequired or ProviderLinkRequired: a ticket, no tokens.
    return NextResponse.json({ data: { signInWithProvider: signedIn } });
  }
  const response = NextResponse.json({ data: { signInWithProvider: withoutTokens(signedIn) } });
  // The account's saved language follows the user to this browser.
  applyCookies(response, [...sessionCookies(signedIn), accountLocaleCookie(signedIn.user.locale)]);
  return response;
}

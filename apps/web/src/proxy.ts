import createMiddleware from 'next-intl/middleware';
import type { NextRequest, NextResponse } from 'next/server';
import { withoutLocaleCookieWrite } from './i18n/locale-cookie';
import { routing } from './i18n/routing';

const intl = createMiddleware(routing);

/**
 * next-intl's middleware reads the locale cookie to redirect `/`, but it also writes the cookie
 * whenever a page of another language is opened, including the Accept-Language redirect. We
 * launch without a consent banner, so the cookie may only exist after the user actively picks a
 * language (the switcher, or the account language at sign-in): drop the middleware's write.
 */
export default function proxy(request: NextRequest): NextResponse {
  return withoutLocaleCookieWrite(intl(request));
}

export const config = {
  // Skip API routes (the GraphQL and auth route handlers), Next internals, Vercel internals and files with an extension.
  matcher: '/((?!api/|_next|_vercel|.*\\..*).*)',
};

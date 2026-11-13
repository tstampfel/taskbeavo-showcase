/**
 * The visitor's IP address as Vercel's edge saw it, or null outside Vercel.
 *
 * Vercel overwrites X-Real-IP and X-Forwarded-For with the connecting client's address
 * rather than appending to what the client sent, so on Vercel these can't be spoofed.
 * Django can't trust them itself (it sees the route handler as the client), so the
 * handler passes the address on with the shared WEB_PROXY_SECRET (upstream.ts).
 */
export function clientIp(headers: Headers): string | null {
  const candidate =
    headers.get('x-real-ip')?.trim() || headers.get('x-forwarded-for')?.split(',')[0]?.trim();
  return candidate || null;
}

/**
 * Hands acceptOffer's client secret to the booking's checkout page in memory, so the first
 * payment needs no second request. Never in the URL, storage or the Apollo cache; a reload
 * loses it and the page asks checkoutSession instead.
 */
const secrets = new Map<string, string>();

export function handOffSecret(bookingId: string, clientSecret: string): void {
  secrets.set(bookingId, clientSecret);
}

/** The secret handed off for this booking, once. */
export function takeSecret(bookingId: string): string | null {
  const secret = secrets.get(bookingId) ?? null;
  secrets.delete(bookingId);
  return secret;
}

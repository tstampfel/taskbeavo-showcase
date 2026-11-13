/**
 * What never goes into a cache saved to disk (mobile's apollo3-cache-persist). These stay in
 * the in-memory cache only and are refetched after a launch: a restored object without them
 * is a partial result, so a cache-first query that selects them goes to the network.
 *
 * - Exact address and location of a task, and its access details (doorbell, floor, gate code,
 *   key): hard rule e shows them only while a booking is active, and a copy on disk outlives it.
 * - Phone numbers, including masked ones.
 * - A helper's home address, and a trader's registered business address, which is often the
 *   obrt owner's home (only the task's poster sees it on an offer; BEAV-192).
 * - Chat message text, once chat is in the schema, and an offer's message (helper-written text
 *   before a booking, treated like chat).
 * - Credentials: session tokens, sign-up tickets, signed upload URLs, Stripe onboarding links,
 *   sign-in nonces, a checkout's Stripe client secret.
 *
 * `'all'` drops the whole object, and every field that points at it. A new schema field whose
 * name looks sensitive must be added here or to PERSIST_CHECKED (persistence.test.ts checks).
 */
export const NOT_PERSISTED: Readonly<Record<string, readonly string[] | 'all'>> = {
  Task: ['address', 'lat', 'lng', 'accessDetails'],
  Offer: ['message', 'helperBusinessAddress'],
  CareContact: ['phone'],
  // The helper of an active booking sees it; a copy on disk on the helper's phone would
  // outlive the booking.
  OnSiteContact: ['name', 'area', 'phone'],
  HelperProfile: [
    'addressStreet',
    'addressPostalCode',
    'businessAddressStreet',
    'businessAddressPostalCode',
  ],
  User: ['phoneNumberMasked'],
  // A poster's business (BEAV-195): an obrt's OIB is its owner's personal OIB, and an
  // obrt's name often holds the owner's name.
  PosterBusiness: ['oibMasked', 'name'],
  PhoneCodeSent: ['phoneNumberMasked'],
  // Signed for a few minutes, and may show an evidence photo.
  Upload: ['url'],
  AuthSession: 'all',
  UploadTarget: 'all',
  PayoutOnboardingLink: 'all',
  SignInNonce: 'all',
  // Its `clientSecret` lets anyone holding it confirm the payment; the booking is cached
  // on its own.
  CheckoutSession: 'all',
  // Sign-up results: their `ticket` creates the account and a session, no code needed.
  AccountDetailsRequired: 'all',
  ProviderLinkRequired: 'all',
};

/** Fields with a sensitive-looking name that were checked and may be saved, with why. */
export const PERSIST_CHECKED: readonly string[] = [
  // The approximate area everyone may see.
  'Task.approximateLat',
  'Task.approximateLng',
  'Task.textMasked',
  // Whether the offer's message had contact details masked: a boolean, no text.
  'Offer.messageMasked',
  'User.phoneVerified',
  'CareContact.noSmartphone',
  // A published task photo, which anyone may load.
  'TaskPhoto.url',
  // An ErrorCode enum key and an ISO country code, not secrets.
  'Upload.rejectionCode',
  'WaitlistEntry.countryCode',
  // A published legal document, public to anyone.
  'LegalDocument.text',
  // Stripe's decline reason as an enum key (insufficient_funds), never the card or its holder.
  'PaymentAttempt.declineCode',
  // City and country of the helper's address: no more precise than the service area.
  'HelperProfile.addressCity',
  'HelperProfile.addressCountry',
  // Town and country of a trader's registered business address: the public profile shows the
  // town anyway (BEAV-192).
  'HelperProfile.businessAddressCity',
  'HelperProfile.businessAddressCountry',
];

type Normalized = Record<string, Record<string, unknown>>;

const fieldName = (storeFieldName: string) => /^[^(:]+/.exec(storeFieldName)?.[0] ?? storeFieldName;

/**
 * A `persistenceMapper` for apollo3-cache-persist: takes the serialized `cache.extract()` and
 * returns it without NOT_PERSISTED. ROOT_MUTATION goes too, since queries never read it and it
 * holds mutation results such as tokens.
 */
export function withoutSensitiveData(serialized: string): string {
  const data = JSON.parse(serialized) as Normalized;
  delete data.ROOT_MUTATION;
  const dropped = new Set<string>();
  for (const [id, object] of Object.entries(data)) {
    if (NOT_PERSISTED[String(object.__typename)] === 'all') {
      dropped.add(id);
      delete data[id];
    }
  }
  for (const object of Object.values(data)) strip(object, dropped);
  return JSON.stringify(data);
}

/** Strips one object in place; false when it is itself dropped whole. */
function strip(object: Record<string, unknown>, dropped: Set<string>): boolean {
  const denied = NOT_PERSISTED[String(object.__typename)];
  if (denied === 'all') return false;
  for (const [key, value] of Object.entries(object)) {
    if ((denied?.includes(fieldName(key)) ?? false) || !keep(value, dropped)) delete object[key];
  }
  return true;
}

/** Whether a field value may stay: it points at no dropped object and holds no denied type. */
function keep(value: unknown, dropped: Set<string>): boolean {
  if (Array.isArray(value)) return value.every((item) => keep(item, dropped));
  if (value === null || typeof value !== 'object') return true;
  const object = value as Record<string, unknown>;
  if (typeof object.__ref === 'string') return !dropped.has(object.__ref);
  // An embedded (unnormalized) object: strip it by its own type.
  return strip(object, dropped);
}

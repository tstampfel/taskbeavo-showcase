---
name: auth-and-accounts
description: Build or change TaskBeavo sign-in and account handling, covering email one-time codes, Google and Apple sign-in, SMS phone verification, the 18+ check, JWT access and refresh tokens (httpOnly cookies on web, SecureStore on mobile, connection_init for WebSockets), log out everywhere, the new-device email, rate limits and lockout, suspended-account limited mode, account recovery, and the user-facing DATA_REQUEST flows (Download my data, account deletion, requests from on-site contacts, consent records and toggles). Use this whenever someone works on login, sign-up, sessions, tokens, phone verification, "this wasn't me", suspension, recovery, settings privacy controls, or asks "how does auth work", even if auth isn't named.
---

# Auth and accounts

Auth lives in Django; web and mobile are clients of it. One account can post and, after helper onboarding, offer. There are no passwords: email codes plus Google and Apple. Everyone verifies a mobile number before posting, offering or chatting, which cuts fake accounts and makes reminders possible. On-site contacts (Mama Kata) never have an account.

**Plan:** `docs/PLAN.md`, sections "Sign-up and login", "A Django monolith, Expo apps and Stripe fit a solo React developer" (auth paragraph), "Reporting, blocking, suspension and appeal", "GDPR flows" and "GDPR: deletion requests and what must be kept". Read them first.

Terms acceptance at sign-up is built with `legal-terms`; the deletion and export background jobs with `gdpr-data-request`.

## 1. Sign-in methods

- **Email code:** `requestEmailCode(email)` sends a 6-digit code (hashed at rest, 10-minute expiry, single use) in the user's or browser's language; `verifyEmailCode(email, code)` returns tokens or a `needsAccountDetails` ticket.
- **Google and Apple:** verify the ID token server-side (audience, issuer, expiry, nonce). Apple may hide the email (relay address) and sends the name only once, so store it on first sign-in. Offer Apple wherever Google is offered on iOS.
- **New accounts** go through one account-details step for every method: name, date-of-birth confirmation that they're **18+** (reject under 18 with `UNDERAGE`), language, country, time zone, and the terms checkbox (`legal-terms`). The user row is created only here.
- Helpers are age-checked again by Stripe identity verification.

## 2. Phone verification

- `requestPhoneCode(phone)` / `verifyPhoneCode(code)` (`backend/accounts/phone.py`), signed in only. `phone` is international or national in the account's country; it is normalised to E.164 with `phonenumbers` and must be a mobile number (`INVALID_PHONE_NUMBER`). Any country qualifies, since posters live anywhere (rule j), except `PHONE_DENIED_REGIONS` (empty; an emergency switch) and +1 countries without a numeric sender in `INFOBIP_SENDER_BY_REGION` (North American carriers drop the alphanumeric `TaskBeavo` sender): both get `PHONE_COUNTRY_NOT_SUPPORTED`. The code is 6 digits, an HMAC bound to account and number, 10 minutes, single use, burnt after 5 wrong guesses (`INVALID_CODE`). An earlier code keeps working until a newer one has actually been sent.
- **One verified number, one account:** `User.phone_digest` (`core.crypto.pii_digest`, keyed with `PII_DIGEST_KEY`) is unique. A number verified elsewhere is refused with `PHONE_NUMBER_IN_USE` only at `verifyPhoneCode`, after the code proved possession, so it can't be used to look up whose a number is. Recycled numbers go to support. Never change `PII_DIGEST_KEY` without running `manage.py recompute_pii_digests` right after.
- **Limits** (`config/settings/base.py`, `PHONE_CODE_*`): a 60 s resend timer per account; sliding one-hour caps of 5 SMS per account, 10 per client address (IPv6 per /64), 5 per number and 20 per country (`PHONE_CODE_COUNTRY_HOURLY_CAPS` raises HR, DE, AT, CH); 500 code SMS a day in total, with an error to Sentry at 80%; 10 checks per account per hour; the mutation limiter adds 3 and 10 calls a minute. Over a limit: `RATE_LIMITED` with `extensions.retryAfterSeconds`. Counters live in the rate-limit Redis, keyed by the number's digest, never the number; while Redis is down both mutations fail closed with `RATE_LIMITED`. Trade-off: anyone who knows a number can fill its per-number cap, so its owner may wait up to an hour.
- **SMS** goes through `core.sms.send_sms` (`SMS_BACKEND`: console locally, memory in tests with `core.sms.outbox`, `core.infobip.InfobipSmsBackend` when deployed). The body is gettext, rendered in the account's language. A provider refusal answers `SMS_NOT_SENT` and gives the resend timer back.
- **The guard.** `postTask`, `sendOffer` and `sendMessage` (and anything else that creates work or contacts people) return `PHONE_NOT_VERIFIED` until verified, and `UNAUTHENTICATED` when signed out. Use the permission on the mutation:

  ```python
  from accounts.permissions import PhoneVerified

  @strawberry.mutation(permission_classes=[PhoneVerified])
  async def post_task(self, info: Info, input: PostTaskInput) -> TaskType: ...
  ```

  or call `require_verified_phone(info.context.user)` first in the resolver or service when it must run beside other checks. It needs no query, so it is safe in async resolvers. Add a test per mutation that an unverified user gets `PHONE_NOT_VERIFIED` (`make_user(..., phone_verified_at=timezone.now())` makes a verified one); `accounts/tests/test_permissions.py` is the pattern.
- The phone is never exposed to other users (rule e): encrypted at rest (`core.crypto.EncryptedTextField`, Fernet under `FIELD_ENCRYPTION_KEY`; rotate with `manage.py reencrypt_fields`), excluded from the admin, and the API has only the owner's `viewer { phoneVerified phoneNumberMasked }` ("+385 ••• 678"). No field may return another user's number. It never goes into logs, errors, spans or rate-limit keys. On account deletion the number, its digest and its codes are deleted, with no fingerprint kept (`gdpr-data-request`).

## 3. Tokens and sessions

- Short-lived JWT access token (about 15 min) and a rotating refresh token stored server-side as a `Session(user, device_name, platform, created_at, last_used_at, revoked_at, refresh_hash)`. Reuse of a rotated refresh token revokes the whole session family.
- **Web:** tokens in httpOnly, `Secure`, `SameSite=Lax` cookies set by a Next.js route handler; never in `localStorage`. **Mobile:** `expo-secure-store`.
- GraphQL HTTP uses `Authorization: Bearer`; `graphql-ws` sends the token in `connection_init`, checked on connect. The client reconnects with a fresh token when it expires.
- **Log out of all devices** revokes every session and sends `force_disconnect` to the Channels group `user.{id}`. Mobile logout also clears the persisted Apollo cache (`mobile-platform`).

## 4. Rate limits, lockout and new-device notice

- At most 5 code requests per hour per email or phone (and per IP), and a 30-minute lockout after 5 wrong codes. Return `RATE_LIMITED` / `LOCKED` with a retry time; never reveal whether an email has an account.
- A sign-in from a new device sends "New login to TaskBeavo from iPhone, Munich" (coarse location from IP, city only) with a **"This wasn't me"** link that logs out everywhere.

## 5. Account states

| State | Can do |
|---|---|
| `ACTIVE` | everything their role allows |
| `TERMS_UPDATE_REQUIRED` | limited mode (`legal-terms`) |
| `SUSPENDED` | log in, see the reason, appeal (`trust-safety`), finish open bookings, download data, delete account |
| `DELETION_PENDING` | nothing new; waiting for open bookings and payouts to close |

Enforce limited modes with one permission class used by every mutation that creates work (`postTask`, `sendOffer`, `acceptOffer`), not screen by screen.

## 6. Account recovery

- Lost phone: sign in by email code, then verify a new number.
- Lost email access: support verifies identity (helpers through Stripe identity) before changing the email; the old address gets a notice. Record the identity check on the support ticket.

## 7. Data requests (user-facing side)

Every request is a `DATA_REQUEST(type, requester_kind, identity_check, received_at, due_at = received + 1 month, completed_at, outcome)`. The Django admin shows the queue sorted by due date.
- **Download my data** (settings, app and web): identity is the logged-in session; creates `DATA_REQUEST(EXPORT)` and enqueues the export job (`gdpr-data-request`), which emails a 7-day link.
- **Delete my account**: confirm by email or SMS code; if funds are held, a payout is pending or a dispute is open, explain why and schedule it (`DELETION_PENDING`); otherwise lock the account, close sessions and WebSockets, and enqueue the deletion job.
- **On-site contact requests** (Mama Kata, by phone, email or web form): staff verify by SMS code to the stored number, then delete or correct the `CARE_CONTACT`; the poster is told. Data still needed for an open booking goes when it closes.
- **Consents**: settings toggles for analytics and marketing email and push, off by default; each change writes a `CONSENT(purpose, granted, version, given_at)` row. Booking and payment messages can't be switched off.

## Tests

```bash
cd backend && uv run pytest accounts privacy -q
```

- Code expiry, single use, 5-per-hour limit, lockout after 5 wrong codes, no account enumeration.
- Google and Apple token verification (bad audience, expired, wrong nonce); Apple relay email.
- Under-18 rejected; account not created before the details step.
- Refresh rotation; reuse revokes the family; log out everywhere closes WebSockets.
- Suspended and terms-required users blocked from creating work but allowed the limited actions.
- `DATA_REQUEST` rows get `due_at` one month out; deletion is deferred while funds are held.

## Checklist

- [ ] Email code, Google and Apple; one account-details step with 18+ and terms
- [ ] Phone verified before posting, offering or chatting (`PhoneVerified` on the mutation); phone never exposed
- [ ] JWT access + rotating refresh; httpOnly cookies on web, SecureStore on mobile; WebSocket auth on connect
- [ ] Log out everywhere revokes sessions and disconnects sockets; new-device email with "This wasn't me"
- [ ] Rate limits and lockout; no account enumeration
- [ ] Limited modes enforced in one permission class
- [ ] Recovery paths with recorded identity checks
- [ ] DATA_REQUEST rows with one-month due date for export, deletion and on-site contact requests; consent toggles logged
- [ ] Strings through `i18n-strings`; no PII in telemetry (`observability`); `compliance-check` run
